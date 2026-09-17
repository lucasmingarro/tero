// Moving the soul-connector with the mouse (Ctrl+Alt + drag), without losing
// click-through.
//
// How GNOME 50 decides who gets a click: by Clutter's *pick* at that moment.
// If the actor under the pointer is reactive, the click goes to the shell; if
// not, it passes through to the window below. There is no longer a
// precomputed "input region" (`_updateRegions()` in layout.js only builds
// struts), so changing `reactive` takes effect from the next click.
//
// The soul-connector itself is never reactive. On top of it there is a
// "handle": a widget the size of the whole soul-connector that only appears
// (and is only reactive) while Ctrl+Alt is held with the pointer over it. The
// rest of the time the soul-connector is click-through, as always.
//
// Three earlier attempts that did not work, so they are not repeated:
//
// - `captured-event` on `global.stage` with a non-reactive actor: the click
//   never arrives. On Wayland it goes straight to the window below.
// - Reading the pointer while letting the click through: the window below
//   also receives the Ctrl+Alt+drag, and tiling-assistant uses exactly Ctrl
//   and Alt during a window drag -- the window tiled while the
//   soul-connector did not move.
// - Making the soul-connector root actor reactive: it works, but only if the
//   click lands exactly on what is drawn (the line of the wave). The root has
//   no background, so the rest of the rectangle does not count as its own.
//   The handle does have a background, so it can be grabbed from any point.
//
// Ctrl+Alt and not Super because Mutter uses Super+drag to move windows.

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

const MODIFIERS = Clutter.ModifierType.CONTROL_MASK | Clutter.ModifierType.MOD1_MASK;
const BUTTON = Clutter.ModifierType.BUTTON1_MASK;

// Querying the pointer is cheap; 30 ms is enough for the drag to feel stuck
// to the hand and for the handle to already be there when the click arrives.
const INTERVAL_MS = 30;

function filePath() {
    return GLib.build_filenamev([GLib.get_user_config_dir(), 'tero', 'soul_connector_position.json']);
}

/** The stored position, or null if there is none or the file is broken. */
export function readPosition() {
    try {
        const [ok, data] = GLib.file_get_contents(filePath());
        if (!ok)
            return null;
        const {x, y} = JSON.parse(new TextDecoder().decode(data));
        if (typeof x !== 'number' || typeof y !== 'number')
            return null;
        return {x, y};
    } catch {
        return null;
    }
}

function savePosition(x, y) {
    try {
        const path = filePath();
        GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
        GLib.file_set_contents(path, JSON.stringify({x: Math.round(x), y: Math.round(y)}));
    } catch (e) {
        logError(e, 'soul-connector: no pude guardar la posición');
    }
}

export class Drag {
    /**
     * @param actor the soul-connector root (St.Widget with FixedLayout, not
     *   reactive)
     * @param onDrop callback with (x, y) when the drag ends
     */
    constructor(actor, onDrop) {
        this._actor = actor;
        this._onDrop = onDrop;
        this._dragging = false;
        this._dx = 0;
        this._dy = 0;

        const [width, height] = actor.get_size();
        this._handle = new St.Widget({
            style_class: 'tero-soul-connector-handle',
            width,
            height,
            reactive: false,
            visible: false,
        });
        // Last child: it sits above the wave and the labels.
        actor.add_child(this._handle);

        this._clickId = this._handle.connect('button-press-event', (_a, event) => {
            if (event.get_button() !== 1)
                return Clutter.EVENT_PROPAGATE;
            const [px, py] = event.get_coords();
            const [ax, ay] = actor.get_position();
            this._dragging = true;
            this._dx = px - ax;
            this._dy = py - ay;
            return Clutter.EVENT_STOP;
        });

        this._timerId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, INTERVAL_MS, () => {
            try {
                this._check();
                return GLib.SOURCE_CONTINUE;
            } catch (e) {
                // It disarms itself and goes back to being click-through: the
                // drag is lost, never the clicks.
                logError(e, 'soul-connector: arrastre desactivado por un error');
                this._showHandle(false);
                this._timerId = 0;
                return GLib.SOURCE_REMOVE;
            }
        });
    }

    _showHandle(show) {
        if (this._handle.reactive !== show)
            this._handle.reactive = show;
        if (this._handle.visible !== show)
            this._handle.visible = show;
    }

    _check() {
        const [px, py, mods] = global.get_pointer();

        if (this._dragging) {
            if ((mods & BUTTON) !== 0) {
                this._actor.set_position(Math.round(px - this._dx), Math.round(py - this._dy));
                return;
            }
            this._dragging = false;
            const [x, y] = this._actor.get_position();
            savePosition(x, y);
            this._onDrop(x, y);
            // No return: if Ctrl+Alt is still held and the pointer is over
            // it, the handle stays and it can be grabbed again right away.
        }

        const [ax, ay] = this._actor.get_position();
        const [width, height] = this._actor.get_size();
        const over = px >= ax && px <= ax + width && py >= ay && py <= ay + height;
        const withKeys = (mods & MODIFIERS) === MODIFIERS;
        this._showHandle(over && withKeys);
    }

    destroy() {
        if (this._timerId) {
            GLib.Source.remove(this._timerId);
            this._timerId = 0;
        }
        if (this._clickId) {
            this._handle.disconnect(this._clickId);
            this._clickId = 0;
        }
        this._handle.destroy();
    }
}
