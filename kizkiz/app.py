#!/usr/bin/env python
# encoding: utf-8
from functools import partial

import rumps

from kizkiz import bluetooth as kbt


class KizKizApp(rumps.App):
    def __init__(self):
        super(KizKizApp, self).__init__('KizKiz')

        self.zik = next(kbt.ZikProxy.find_all_ziks())
        self.zik.register(self.status_update)
        self.zik.connect()

    def build_menu(self):
        self.menu.clear()

        zik = self.zik
        bs = zik.BatteryState

        self.menu.update([
            'Connected: {name}'.format(name=zik.name),
            rumps.MenuItem('Battery: {0}'.format({
                bs.CHARGING: 'Charging',
                bs.CALC: 'Calculating...',
                bs.IN_USE: '{0}%'.format(zik.s_battery_level)
            }[self.zik.s_battery_state])),
            rumps.separator,
            rumps.MenuItem(
                'Noise Cancellation',
                callback=self.on_noise_cancellation
            ),
            rumps.MenuItem(
                'Lou Reed Mode',
                callback=self.on_lou_reed_mode
            ),
            ('EQ', [
                rumps.MenuItem(p[1], callback=partial(self.on_eq, p[1], p[0]))
                for p in zik.s_eq_presets
            ] + [
                rumps.separator,
                rumps.MenuItem(
                    'Disabled',
                    callback=partial(self.on_eq, None, None)
                )
            ]),
            ('Advanced', [
                'Firmware Version: {0}'.format(zik.s_version)
            ]),
            rumps.separator,
            rumps.MenuItem('Quit', callback=self.on_quit)
        ])

        # Update some menu states, since unfortunately the
        # current version of rumps doesn't let you set it in the
        # constructor.
        self.menu['Noise Cancellation'].state = zik.s_noise_cancellation
        self.menu['Lou Reed Mode'].state = zik.s_lou_reed_mode

        for eq_title, eq_item in self.menu['EQ'].items():
            if not eq_title:
                # Skip over seperators
                continue

            if zik.s_eq_enabled:
                eq_item.state = eq_title == zik.s_eq_preset_name
            else:
                eq_item.state = eq_title == 'Disabled'

    def on_quit(self, _):
        rumps.quit_application()

    def on_noise_cancellation(self, sender):
        self.zik.s_noise_cancellation = not sender.state
        sender.state = not sender.state

    def on_eq(self, name, id_, sender):
        if name is None and id_ is None:
            # A special case for the 'Disabled' preset.
            self.zik.s_eq_enabled = False
        else:
            self.zik.s_eq_enabled = True
            print('tried to set to', id_)
            self.zik.s_eq_preset_id = id_

        # Rebuild the EQ menu
        self.build_menu()

    def on_lou_reed_mode(self, sender):
        self.zik.s_lou_reed_mode = not sender.state
        sender.state = not sender.state

    def status_update(self, zik):
        self.build_menu()

if __name__ == "__main__":
    KizKizApp().run()
