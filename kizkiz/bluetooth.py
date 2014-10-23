#!/usr/bin/env python
# encoding: utf-8
"""
Sources:

1: https://developer.apple.com/library/mac/documentation/IOBluetooth/Reference
    /IOBluetoothRFCOMMChannel_reference/index.html
2: https://developer.apple.com/library/mac/documentation/Cocoa/Conceptual
    /ObjCRuntimeGuide/Articles/ocrtTypeEncodings.html
3: IOBluetooth.framework/Headers/objc/IOBluetoothRFCOMMChannel.h
"""
import ctypes
import struct
from cStringIO import StringIO

import objc
objc.setVerbose(1)
import Foundation as foundation
import xmltodict


class ZikError(Exception):
    pass


class ZikDeviceError(Exception):
    pass


class ZikProtocolError(Exception):
    pass


class Proxy(dict):
    def __getattr__(self, key):
        return self[key]


class BluetoothProxy(Proxy):
    def __init__(self):
        objc.loadBundle(
            'IOBluetooth',
            self,
            bundle_path=u'/System/Library/Frameworks/IOBluetooth.framework'
        )

        # Thank you mailing list archives. Figured out how to
        # do these signatures from a 2005 mailing list post.
        objc.setSignatureForSelector(
            'IOBluetoothSDPServiceRecord',
            'getRFCOMMChannelID:',
            'i12@0:o^C'
        )
        objc.setSignatureForSelector(
            'IOBluetoothDevice',
            'openRFCOMMChannelSync:withChannelID:delegate:',
            'i16@0:o^@C@'
        )
        objc.setSignatureForSelector(
            'IOBluetoothRFCOMMChannel',
            'writeSync:length:',
            'i16@0:*S'
        )


class ZikProxy(object):
    ZIK_SERVICE_NAME = 'Parrot RFcomm service'
    ZIK_SERVICE_UUID = '0ef0f502-f0ee-46c9-986c-54ed027807fb'

    ZIK_SYS_BATTERY_GET = '/api/system/battery/get'
    ZIK_SYS_DEVICE_TYPE_GET = '/api/system/device_type/get'
    ZIK_SYS_VERSION_GET = '/api/software/version/get'

    ZIK_SYS_HEAD_DETECT_GET = '/api/system/head_detection/enabled/get'
    ZIK_SYS_HEAD_DETECT_SET = '/api/system/head_detection/enabled/set'

    ZIK_AUDIO_NOISE_GET = '/api/audio/noise_cancellation/enabled/get'
    ZIK_AUDIO_NOISE_SET = '/api/audio/noise_cancellation/enabled/set'

    ZIK_AUDIO_SPECIFIC_MODE_GET = '/api/audio/specific_mode/enabled/get'
    ZIK_AUDIO_SPECIFIC_MODE_SET = '/api/audio/specific_mode/enabled/set'

    ZIK_EQ_GET = '/api/audio/equalizer/get'
    ZIK_EQ_PRESETS_GET = '/api/audio/equalizer/presets_list/get'
    ZIK_EQ_ENABLE_SET = '/api/audio/equalizer/enabled/set'
    ZIK_EQ_PRESET_SET = '/api/audio/equalizer/preset_id/set'

    kIOReturnSuccess = 0

    class BatteryState(object):
        #: The battery is currently in use, meaning the headphones
        #: are unplugged and on.
        IN_USE = 10
        #: The headphones are plugged into a USB port and are charging.
        CHARGING = 20
        #: The headphones have been unplugged, but they have not yet
        #: calculated the estimated battery life.
        CALC = 30

    def __init__(self, bp, device):
        self.device = device
        self.bp = bp

        self._handlers = set()
        #: The RFCOMM channel.
        self._channel = None
        #: The RFCOMM channel event listener.
        self._listener = None

        self._s_version = None
        self._s_battery_level = 0
        self._s_battery_state = ZikProxy.BatteryState.IN_USE
        self._s_noise_cancellation = False
        self._s_eq_presets = []
        self._s_eq_preset_id = None
        self._s_eq_enabled = False
        self._s_specific_mode = None

        for service in device.services():
            if service.getServiceName() == ZikProxy.ZIK_SERVICE_NAME:
                self.service = service
                break
        else:
            raise ZikDeviceError('no rfcomm service')

    @classmethod
    def find_all_ziks(cls):
        """
        Yields all of the Parrot Ziks already paired to this machine
        """
        bp = BluetoothProxy()
        devices = bp.IOBluetoothDevice.pairedDevices()

        for device in devices:
            if device.addressString().startswith('90-03'):
                yield cls(bp, device)

    def connect(self):
        """
        Establish an RFCOMM channel to the discovered Zik device, and
        query for a full status update once connected.
        """
        err, port = self.service.getRFCOMMChannelID_(None)
        if err != ZikProxy.kIOReturnSuccess:
            raise ZikDeviceError('could not find rfcomm port')

        listener = ZikChannelDelegate.alloc().initWithDelegate_(self)

        err, chan = self.device.openRFCOMMChannelSync_withChannelID_delegate_(
            None,
            port,
            listener
        )
        if err != ZikProxy.kIOReturnSuccess:
            raise ZikDeviceError('could not open rfcomm service channel')

        listener.listen(chan)

        # We *MUST* keep these around, or we'll segfault when objc
        # tries to call the delegates and they've been garbage
        # collected.
        self._listener = listener
        self._channel = chan

        # Do the handshake, which is the length (00 03) and the packet
        # type (00). From this point, we can start sending regular
        # requests.
        self._write('\x00\x03\x00')
        self.update_status()

    @property
    def name(self):
        """
        The name of this Zik device (as advertised by BT).
        """
        return self.device.name()

    def _write(self, data):
        """
        Write raw data to the RFCOMM channel.
        """
        err = self._channel.writeSync_length_(data, len(data))
        if err != ZikProxy.kIOReturnSuccess:
            raise ZikDeviceError('could not write data to channel!')

    def _request(self, method, endpoint, value=None):
        """
        Constructs a Zik API request. Method can be either GET or
        SET.

        This API is HTTP-ish.

        :param method: The request method (GET or SET)
        :param endpoint: The path to request.
        :param value: If making a SET request, this is the value to
                      set.
        """
        message = bytearray()
        message.extend(method)
        message.append(' ')
        message.extend(str(endpoint))

        if value is not None:
            # For convienience, convert True/False.
            message.extend('?arg={value}'.format(value={
                True: 'true',
                False: 'false'
            }.get(value, value)))

        self._write(
            struct.pack('>HB{count}s'.format(
                # +3 for the header, which is included in the size.
                count=len(message)
            ), len(message) + 3, 0x80, str(message))
        )

    def _handle_packet(self, packet, length):
        """
        Parse and handle an incoming packet of data over the Zik's
        Bluetooth RFCOMM channel.

        .. note::

            It's possible for a single packet to exceed the size of
            the devices MTU, requiring multiple messages to send.
            However, it does not appear that the Zik is capable of
            fragmented messages, so this method has been simplified
            to assume we always get the complete message.

        :param packet: A byte string containing the complete packet.
        :param length: The expected length of the complete packet.
        """
        io = StringIO(packet)

        packet_size, packet_type = struct.unpack('>HB', io.read(3))

        if packet_type == 0x00:
            # Just the handshack ACK, nothing to really do.
            return
        elif packet_type == 0x80:
            # GET Response.
            x, y, z = struct.unpack('>BBH', io.read(4))
            left = packet_size - io.tell()

            # The packet body is an XML response, with either an
            # "answer" node (in response to a request) or a
            # "notify" node (periodic or in response to a device
            # event).
            packet_body = xmltodict.parse(
                struct.unpack(
                    '>{0}s'.format(left),
                    io.read(left)
                )[0]
            )

            if 'notify' in packet_body:
                self._request('GET', packet_body['notify']['@path'])
                return

            try:
                n = packet_body['answer']
            except KeyError:
                raise ZikProtocolError('unknown message type')

            # The path is the api endpoint that's been SET or GET'd
            # echo'd back at us.
            path = n['@path']

            if path == self.ZIK_SYS_BATTERY_GET:
                # Handles battery level and state updates.
                battery = n['system']['battery']

                if battery['@state'] == 'charging':
                    self._s_battery_level = -1
                    self._s_battery_state = ZikProxy.BatteryState.CHARGING
                else:
                    self._s_battery_state = ZikProxy.BatteryState.IN_USE
                    if battery['@level'] == '':
                        # We can be IN_USE but not yet know what the battery
                        # level is (likely because it was just unplugged.)
                        # The Zik will send another NOTIFY event when it
                        # knows what the real level is.
                        self._s_battery_level = 0
                        self._s_battery_state = ZikProxy.BatteryState.CALC
                    else:
                        self._s_battery_level = int(battery['@level'])
            elif path == self.ZIK_AUDIO_NOISE_GET:
                # Handles noise cancellation updates.
                enabled = n['audio']['noise_cancellation']['@enabled']
                self._s_noise_cancellation = enabled == 'true'
            elif path == self.ZIK_SYS_VERSION_GET:
                # Handles system software version updates.
                self._s_version = n['software']['@version']
            elif path == self.ZIK_AUDIO_SPECIFIC_MODE_GET:
                # No idea what this actually does!
                enabled = n['audio']['specific_mode']['@enabled']
                self._s_specific_mode = enabled == 'true'
            elif path == self.ZIK_EQ_PRESETS_GET:
                # Handles EQ preset lists.
                eq = n['audio']['equalizer']
                presets = eq['presets_list']['preset']
                self._s_eq_presets = [
                    (int(p['@id']), p['@name']) for p in presets
                ]
            elif path == self.ZIK_EQ_GET:
                eq = n['audio']['equalizer']
                self._s_eq_enabled = eq['@enabled'] == 'true'
                self._s_eq_preset_id = int(eq['@preset_id'])
            else:
                print(packet_body)

            # Tell anyone whose interested that our state has probably been
            # changed.
            for handler in self._handlers:
                handler(self)

    def update_status(self):
        """
        Triggers a full status update, querying the Parrot Zik for all
        known endpoints.

        .. note::

            Called automatically when a connection to the Parrot Zik
            is established - there's usually no need to call this
            manually.
        """
        self._request('GET', self.ZIK_SYS_VERSION_GET)
        self._request('GET', self.ZIK_SYS_BATTERY_GET)
        self._request('GET', self.ZIK_AUDIO_NOISE_GET)
        self._request('GET', self.ZIK_AUDIO_SPECIFIC_MODE_GET)
        self._request('GET', self.ZIK_EQ_GET)
        self._request('GET', self.ZIK_EQ_PRESETS_GET)

    def register(self, handler):
        """
        Register a callback. When the status of the Parrot Zik changes,
        all registered callbacks will be trigged.
        """
        self._handlers.add(handler)

    def unregister(self, handler):
        """
        Remove a previously registered callback.
        """
        self._handlers.remove(handler)

    @property
    def s_version(self):
        """
        The version of the software running on the Parrot Zik.
        """
        return self._s_version

    @property
    def s_battery_level(self):
        """
        The battery level as a percentage, if one is available.
        It is possible for this value to be 0, in which case the
        device may be charging or calculating the estimated time
        remaining.
        """
        return self._s_battery_level

    @property
    def s_battery_state(self):
        """
        The battery state. See :class:`ZikProxy.BatteryState` for
        possible values.
        """
        return self._s_battery_state

    @property
    def s_noise_cancellation(self):
        """
        The state of the parrot zik's noise cancellation feature.
        The value can be toggled by setting this to `True` or `False`.
        """
        return self._s_noise_cancellation

    @s_noise_cancellation.setter
    def s_noise_cancellation(self, value):
        assert(isinstance(value, bool))
        self._s_noise_cancellation = value
        self._request('SET', self.ZIK_AUDIO_NOISE_SET, value=value)

    @property
    def s_specific_mode(self):
        return self._s_specific_mode

    @property
    def s_eq_presets(self):
        return self._s_eq_presets[:]

    @property
    def s_eq_preset_id(self):
        return self._s_eq_preset_id

    @s_eq_preset_id.setter
    def s_eq_preset_id(self, value):
        assert(isinstance(value, (int, long)))
        self._s_eq_preset_id = value
        self._request('SET', self.ZIK_EQ_PRESET_SET, value=value)

    @property
    def s_eq_preset_name(self):
        preset = self.preset_by_id(self.s_eq_preset_id)
        return preset[1] if preset else None

    @property
    def s_eq_enabled(self):
        return self._s_eq_enabled

    @s_eq_enabled.setter
    def s_eq_enabled(self, value):
        assert(isinstance(value, bool))
        self._s_eq_enabled = value
        self._request('SET', self.ZIK_EQ_ENABLE_SET, value=value)

    def preset_by_id(self, id_):
        for preset in self.s_eq_presets:
            if preset[0] == id_:
                return preset


class ZikChannelDelegate(foundation.NSObject):
    @objc.signature('@@:@')
    def initWithDelegate_(self, cb):
        super(ZikChannelDelegate, self).init()
        self.callback = cb
        return self

    @objc.signature('v@:@^vS')
    def rfcommChannelData_data_length_(self, channel, data, length):
        # Ideally, this method would just use the * signature to get
        # a character string instead of a void pointer. However, some
        # versions of pyobjc see a NULL byte in the string and stop
        # there.
        packet = ctypes.string_at(data, length)
        self.callback._handle_packet(packet, length)

    def listen(self, channel):
        channel.setDelegate_(self)

if __name__ == '__main__':
    zik = next(ZikProxy.find_all_ziks())
    zik.connect()
