// Tero toggle in GNOME's quick settings menu (the one that opens from the top
// right, next to WiFi/Bluetooth/etc. -- an explicit request from the user, who
// showed a screenshot of that menu). It controls the systemd user service
// (see systemd/tero.service) instead of killing processes by hand:
// start/restart/stop, plus a direct link to the log to see what is going on
// without opening a terminal.
//
// Why it does not live inside extension.js: it is a piece independent of the
// soul-connector (the wave) -- it can stay visible even if the user
// uninstalls or tries another version of the wave, and vice versa. It is
// instantiated from extension.js's `enable()`/`disable()` like everything
// else.
//
// The state (is it running?) is re-read every few seconds with `systemctl
// --user is-active` instead of assuming the last click was the truth -- if
// health.py shuts Tero down on its own (critical RAM) or somebody stops it
// from a terminal, the toggle has to notice anyway.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

const QuickSettingsMenu = Main.panel.statusArea.quickSettings;

const POLL_INTERVAL_S = 4;
const LOG_PATH = GLib.build_filenamev(
    [GLib.get_home_dir(), 'proyectos', 'tero', 'logs', 'tero.log']);

function _run(argv) {
    // Fire-and-forget on purpose: there is no need to wait for the result to
    // refresh the state, the next poll already shows the real one.
    try {
        const process = new Gio.Subprocess({argv, flags: Gio.SubprocessFlags.NONE});
        process.init(null);
    } catch (error) {
        logError(error, `Tero (panel): ${argv.join(' ')}`);
    }
}

const _systemctl = action => _run(['systemctl', '--user', action, 'tero']);

const TeroToggle = GObject.registerClass(
class TeroToggle extends QuickSettings.QuickMenuToggle {
    _init() {
        super._init({
            title: 'Tero',
            iconName: 'audio-input-microphone-symbolic',
            toggleMode: false, // the real state comes from systemctl, not the click
        });

        this.menu.setHeader('audio-input-microphone-symbolic', 'Tero');

        const section = new PopupMenu.PopupMenuSection();
        this._startItem = section.addAction('Iniciar', () => _systemctl('start'));
        this._restartItem = section.addAction('Reiniciar', () => _systemctl('restart'));
        this._stopItem = section.addAction('Cerrar', () => _systemctl('stop'));
        this.menu.addMenuItem(section);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Ver log', () => _run(['xdg-open', LOG_PATH]));

        // A direct click on the icon (without opening the submenu): the same
        // one-tap gesture as Wifi/Bluetooth -- turns it on if it is off, off
        // if it is on.
        this.connect('clicked', () => _systemctl(this._active ? 'stop' : 'start'));

        this._active = null;
        this._updateState();
        this._pollId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, POLL_INTERVAL_S, () => {
                this._updateState();
                return GLib.SOURCE_CONTINUE;
            });
        this.connect('destroy', () => {
            if (this._pollId) {
                GLib.Source.remove(this._pollId);
                this._pollId = 0;
            }
        });
    }

    _updateState() {
        let process;
        try {
            process = new Gio.Subprocess({
                argv: ['systemctl', '--user', 'is-active', 'tero'],
                flags: Gio.SubprocessFlags.STDOUT_PIPE,
            });
            process.init(null);
        } catch (error) {
            logError(error, 'Tero (panel): systemctl --user is-active tero');
            return;
        }
        process.communicate_utf8_async(null, null, (_p, result) => {
            let output = '';
            try {
                [, output] = process.communicate_utf8_finish(result);
            } catch (error) {
                return; // the toggle may have been destroyed while waiting
            }
            const active = output.trim() === 'active';
            if (active === this._active)
                return;
            this._active = active;
            this.checked = active;
            this.subtitle = active ? 'Escuchando' : 'Apagado';
            this._startItem.visible = !active;
            this._restartItem.visible = active;
            this._stopItem.visible = active;
        });
    }
});

export const TeroIndicator = GObject.registerClass(
class TeroIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();
        this._toggle = new TeroToggle();
        this.quickSettingsItems.push(this._toggle);
        QuickSettingsMenu.addExternalIndicator(this);
    }

    // Not called `destroy`: that is GObject's own method, and calling
    // this.destroy() from inside an override named destroy() recurses
    // forever.
    teardown() {
        this.quickSettingsItems.forEach(item => item.destroy());
        this.destroy();
    }
});
