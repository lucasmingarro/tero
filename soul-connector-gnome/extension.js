// Tero's soul-connector as a GNOME Shell extension.
//
// Why it exists: the original soul-connector (soul_connector/, pywebview +
// QtWebEngine) takes ~1.3 GB of RAM to draw a wave, because it spins up a
// whole Chromium. Here the drawing runs inside gnome-shell, which is already
// in memory, so the extra cost is essentially the two sines computed per
// frame.
//
// As a bonus it solves an old problem: "always on top" never worked properly
// under Mutter (see CLAUDE.md), and the pywebview soul-connector fought it by
// calling wmctrl in a loop while Tero spoke. Being part of the shell there is
// nothing to fight: the actor lives in the chrome layer, above the windows,
// always.
//
// It does not touch the daemon: it is just one more client of the level
// WebSocket.

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {Wave, ORIGINAL_COLORS, baseline} from './wave.js';
import {Bar} from './bar.js';
import {Particles} from './particles.js';
import {Drag, readPosition} from './move.js';
import {Link} from './link.js';
import {TeroIndicator} from './panel.js';

const WIDTH = 260;
const HEIGHT = 74;
const MARGIN = 20;

// D-Bus so tools/move_window.py can move any window (X11 or native Wayland)
// without going through wmctrl, which can only touch X11/XWayland windows
// from outside the compositor. In here Meta.Window.move_to_monitor() has no
// such limitation -- see [[proyecto-mover-ventana-monitor]] in the project
// memory, validated live in the nested shell on 2026-09-15 before adding it
// here.
const _WINDOWS_IFACE = `
<node>
  <interface name="org.gnome.Shell.Extensions.Tero">
    <method name="ListWindows">
      <arg type="s" direction="out" name="json" />
    </method>
    <method name="MoveWindowToMonitor">
      <arg type="s" direction="in" name="app" />
      <arg type="i" direction="in" name="monitor" />
      <arg type="s" direction="out" name="result" />
    </method>
  </interface>
</node>`;

// The bar itself is 1px, but the glow and the dot at the tip spill out quite
// a bit: without this height the drawing gets clipped.
const BAR_HEIGHT = 20;

// Measured with the DOM over soul_connector/index.html, which is the
// original: in a 260x74 box the times take y 23.5-33.5, the bar line goes at
// y=34 (i.e. resting on the wave axis, which is why they read as a single
// line) and the song name at y 39-51.
const SONG_Y = 39;

const FPS = 60;
const IDLE_FPS = 30; // at rest the wave barely breathes: no need for more

// The same colors and constants as soul_connector/index.html, so the look
// does not change when migrating.
const COLORS = {
    idle: [0x4a, 0x44, 0x58],
    listening: [0xff, 0xff, 0xff],
    thinking: [0xc7, 0x7d, 0xff],
    speaking: null, // the library's original multicolor
    music: [0x5c, 0xff, 0xd4],
    // When Tero delegates to Codex/ChatGPT (phase 4): the wave turns cyan and
    // colored particles rise from its axis, as the user asked ("que dé la
    // idea de asteroides") -- see particles.js.
    codex: [0x00, 0xea, 0xff],
};

const IDLE_AMPLITUDE = 0.15;
const THINKING_AMPLITUDE = 0.45;
const CODEX_AMPLITUDE = 0.55;

// Asymmetric smoothing: fast attack, slower decay. Raw RMS jitters, and this
// is what separates "looks pro" from "looks amateur".
const ATTACK = 0.7;
const DECAY = 0.25;

const FOLLOW_LEVEL = ['speaking', 'music', 'listening'];
const HIDE_PAUSED_MS = 30000;

function formatTime(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

class SoulConnector {
    constructor() {
        this._state = 'idle';
        this._rawLevel = 0;
        this._smoothLevel = IDLE_AMPLITUDE;
        this._speed = 0.06;
        this._wave = new Wave();
        this._bar = new Bar();
        this._particles = new Particles();

        this._song = null;
        this._fraction = 0;
        this._baseProgress = 0;
        this._durationMs = 0;
        this._receivedAt = 0;
        this._pausedSince = 0;

        // null = not known yet (just enabled): that way no fleeting "Tero
        // apagado" shows up while the first attempt is made.
        this._connected = null;
        this._loading = null;

        this._buildActors();
        this._animationId = 0;
        this._currentFps = 0;
        this._animate(IDLE_FPS);

        this._link = new Link(m => this._onMessage(m), c => this._onConnectionChanged(c));
        this._link.connect();
    }

    _buildActors() {
        this._root = new St.Widget({
            width: WIDTH,
            height: HEIGHT,
            reactive: false,
            layout_manager: new Clutter.FixedLayout(),
        });

        this._area = new St.DrawingArea({width: WIDTH, height: HEIGHT});
        this._area.connect('repaint', a => this._paint(a));
        this._root.add_child(this._area);

        // Two layers for the "codex" particles, not one: the halo comes from
        // applying a real blur (GPU, Shell.BlurEffect -- the same one
        // gnome-shell uses for the overview background) to THIS WHOLE layer,
        // and the crisp core goes separately, without blur, on top -- just
        // like `box-shadow` does in CSS (blurred shadow behind, crisp element
        // in front). Approximating the blur by hand with Cairo (gradient,
        // rings) was tried and both times it looked like a shaded sphere --
        // see particles.js.
        this._glowArea = new St.DrawingArea({width: WIDTH, height: HEIGHT});
        this._glowArea.connect('repaint', a => this._paintParticleGlow(a));
        // A smaller radius than the first attempt (10): a wide blur over a
        // 2-3px dot dilutes the brightness too much (tested live, it was
        // barely visible). With this the halo stays more concentrated.
        this._blurEffect = new Shell.BlurEffect({
            radius: 6, brightness: 1.0, mode: Shell.BlurMode.ACTOR,
        });
        this._glowArea.add_effect(this._blurEffect);
        this._root.add_child(this._glowArea);

        this._particleCoreArea = new St.DrawingArea({width: WIDTH, height: HEIGHT});
        this._particleCoreArea.connect('repaint', a => this._paintParticleCores(a));
        this._root.add_child(this._particleCoreArea);

        this._songLabel = new St.Label({
            style_class: 'tero-soul-connector-song',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._songLabel.clutter_text.set_line_wrap(false);
        this._songLabel.clutter_text.set_ellipsize(3 /* END */);
        this._songLabel.set_position(10, SONG_Y);
        this._songLabel.set_width(WIDTH - 20);
        this._songLabel.opacity = 0;
        this._root.add_child(this._songLabel);

        // The same place as the song name: while Tero is starting up or is off
        // there is no song to show, so they do not compete.
        this._noticeLabel = new St.Label({
            style_class: 'tero-soul-connector-notice',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._noticeLabel.clutter_text.set_ellipsize(3 /* END */);
        this._noticeLabel.set_position(10, SONG_Y);
        this._noticeLabel.set_width(WIDTH - 20);
        this._noticeLabel.opacity = 0;
        this._root.add_child(this._noticeLabel);

        this._progressRow = new St.BoxLayout({
            style_class: 'tero-soul-connector-progress',
            width: WIDTH - 20,
        });
        // The row is centered on the wave axis, so that the bar line falls
        // exactly on top of the thread the wave draws and the two read as a
        // single one. The times end up resting just above that line, as in the
        // original.
        this._progressRow.set_position(
            10, Math.round(baseline(HEIGHT) - BAR_HEIGHT / 2));
        this._progressRow.opacity = 0;

        this._currentTime = new St.Label({style_class: 'tero-soul-connector-time'});
        this._track = new St.DrawingArea({
            height: BAR_HEIGHT,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._track.connect('repaint', a => this._paintBar(a));
        this._totalTime = new St.Label({style_class: 'tero-soul-connector-time'});

        this._progressRow.add_child(this._currentTime);
        this._progressRow.add_child(this._track);
        this._progressRow.add_child(this._totalTime);
        this._root.add_child(this._progressRow);
    }

    get actor() {
        return this._root;
    }

    _colors() {
        const color = COLORS[this._state];
        if (color === null || color === undefined)
            return ORIGINAL_COLORS;
        return [color, color, color];
    }

    _paint(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        this._wave.draw(cr, width, height, this._smoothLevel, this._speed,
            this._colors());
        cr.$dispose();
    }

    _paintParticleGlow(area) {
        const cr = area.get_context();
        const [, height] = area.get_surface_size();
        this._particles.drawGlow(cr, baseline(height));
        cr.$dispose();
    }

    _paintParticleCores(area) {
        const cr = area.get_context();
        const [, height] = area.get_surface_size();
        this._particles.drawCores(cr, baseline(height));
        cr.$dispose();
    }

    _paintBar(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        const paused = !this._song || !this._song.playing;
        // The row is centered on the wave axis, so the center of this area
        // already *is* the height of the line.
        this._bar.draw(cr, width, height / 2, this._fraction, paused);
        cr.$dispose();
    }

    _targetAmplitude() {
        if (FOLLOW_LEVEL.includes(this._state))
            return this._rawLevel;
        if (this._state === 'thinking')
            return THINKING_AMPLITUDE;
        if (this._state === 'codex')
            return CODEX_AMPLITUDE;
        return IDLE_AMPLITUDE;
    }

    _animate(fps) {
        if (this._animationId)
            GLib.Source.remove(this._animationId);
        this._currentFps = fps;
        this._animationId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, Math.round(1000 / fps), () => this._frame());
    }

    _frame() {
        const target = this._targetAmplitude();
        const rate = target > this._smoothLevel ? ATTACK : DECAY;
        this._smoothLevel += (target - this._smoothLevel) * rate;

        // The speed follows the level too: a wave that always moves the same
        // regardless of how loud the voice is, is exactly what looks decoupled
        // from the audio.
        if (FOLLOW_LEVEL.includes(this._state))
            this._speed = 0.04 + this._smoothLevel * 0.18;
        else if (this._state === 'thinking')
            this._speed = 0.12;
        else if (this._state === 'codex')
            this._speed = 0.16; // livelier than "thinking": it is the "energized" state
        else
            this._speed = 0.06;

        this._area.queue_repaint();
        // Once here, not inside each _paintParticle*: both layers have to
        // paint the SAME list of particles in the same state, not each one its
        // own (see particles.js).
        this._particles.update(this._state === 'codex', WIDTH);
        this._glowArea.queue_repaint();
        this._particleCoreArea.queue_repaint();
        this._updateProgress();
        return GLib.SOURCE_CONTINUE;
    }

    _onConnectionChanged(connected) {
        this._connected = connected;
        if (!connected) {
            // Whatever the daemon last sent is no longer valid: without this
            // the wave stayed frozen on "speaking" or with the previous song.
            this._loading = null;
            this._rawLevel = 0;
            this._applyState('idle');
            this._applySong(null);
        }
        this._updateNotice();
    }

    _updateNotice() {
        let text = null;
        if (this._connected === false)
            text = 'Tero apagado';
        else if (this._loading)
            text = `${this._loading.text} · ${Math.round(this._loading.progress * 100)}%`;

        if (text) {
            this._noticeLabel.text = text;
            this._noticeLabel.ease({opacity: 255, duration: 400});
        } else {
            this._noticeLabel.ease({opacity: 0, duration: 400});
        }
    }

    _onMessage(message) {
        if (message.loading !== undefined) {
            this._loading = message.loading;
            this._updateNotice();
        }
        if (message.state !== undefined)
            this._applyState(message.state);
        if (message.level !== undefined)
            this._rawLevel = message.level;
        if (message.song !== undefined)
            this._applySong(message.song);
    }

    _applyState(next) {
        this._state = next;
        // At 30 fps the resting state looks the same and saves gnome-shell
        // half the frames, and gnome-shell is the process that draws the whole
        // desktop -- here the cost is not paid by a separate process.
        const fps = next === 'idle' ? IDLE_FPS : FPS;
        if (fps !== this._currentFps)
            this._animate(fps);
    }

    _applySong(info) {
        this._song = info;
        if (!info) {
            this._songLabel.ease({opacity: 0, duration: 600});
            this._progressRow.ease({opacity: 0, duration: 600});
            return;
        }
        this._songLabel.text = info.text;
        this._baseProgress = info.progress_ms;
        this._durationMs = info.duration_ms;
        this._receivedAt = GLib.get_monotonic_time() / 1000;
        this._songLabel.ease({opacity: 180, duration: 600});
        // With no real duration (a live YouTube channel, not a song with a
        // start and an end) the time/bar row is hidden -- showing it stuck at
        // 0:00/0:00 looks broken, not "live".
        this._progressRow.ease({opacity: info.duration_ms ? 255 : 0, duration: 600});
    }

    _updateProgress() {
        if (!this._song || !this._durationMs)
            return;
        const paused = !this._song.playing;
        const elapsed = paused
            ? 0
            : GLib.get_monotonic_time() / 1000 - this._receivedAt;
        const progress = Math.min(this._durationMs, this._baseProgress + elapsed);
        this._currentTime.text = formatTime(progress);
        this._totalTime.text = formatTime(this._durationMs);
        this._fraction = progress / this._durationMs;
        // Always, not only when the fraction changes: the glow cycles color,
        // the brightness breathes and while paused the dot beats. All of that
        // moves even if the song is stuck on the same second.
        this._track.queue_repaint();
    }

    destroy() {
        if (this._animationId) {
            GLib.Source.remove(this._animationId);
            this._animationId = 0;
        }
        this._link.destroy();
        this._root.destroy();
    }
}

export default class SoulConnectorExtension extends Extension {
    enable() {
        this._soulConnector = new SoulConnector();
        // addChrome (and not a loose actor in uiGroup) is what puts it in the
        // chrome layer: above the windows and handled properly when entering
        // and leaving fullscreen.
        //
        // Careful: the old `affectsInputRegion: false` parameter no longer
        // exists in GNOME 50 (it throws "Unrecognized parameter" and the
        // extension does not load). Clicks passing through now comes from the
        // actor being `reactive: false`, which is how it is built in
        // SoulConnector.
        Main.layoutManager.addChrome(this._soulConnector.actor, {
            trackFullscreen: true,
        });
        this._position = readPosition();
        this._place();
        this._drag = new Drag(this._soulConnector.actor, (x, y) => {
            this._position = {x, y};
        });
        this._monitorsId = Main.layoutManager.connect(
            'monitors-changed', () => this._place());
        // And this is the one that really matters. At login the extension can
        // be enabled BEFORE the dock reserves its strip: at that point the
        // work area is still the whole monitor, the soul-connector is placed
        // at the very bottom and stays covered forever, because
        // `monitors-changed` does not fire for that. `workareas-changed` does,
        // so the soul-connector rearranges itself when the dock appears, goes
        // away, changes side or changes size.
        this._areasId = global.display.connect(
            'workareas-changed', () => this._place());

        this._panel = new TeroIndicator();

        this._windowsDbus = Gio.DBusExportedObject.wrapJSObject(_WINDOWS_IFACE, this);
        this._windowsDbus.export(
            Gio.DBus.session, '/org/gnome/Shell/Extensions/Tero');
    }

    // wm_class first: the title changes with every open tab/document (the same
    // reason _youtube_screen.py identifies its window by PID and not by
    // title). If nothing matches by wm_class it falls back to searching the
    // title, for apps without a useful wm_class.
    _findWindow(app) {
        const wanted = app.toLowerCase();
        const windows = global.get_window_actors().map(w => w.meta_window);
        return windows.find(w => (w.get_wm_class() || '').toLowerCase().includes(wanted))
            || windows.find(w => (w.get_title() || '').toLowerCase().includes(wanted));
    }

    ListWindows() {
        const windows = global.get_window_actors().map(w => ({
            title: w.meta_window.get_title(),
            wm_class: w.meta_window.get_wm_class(),
            monitor: w.meta_window.get_monitor(),
        }));
        return JSON.stringify(windows);
    }

    // The error strings are the wire protocol with tools/move_window.py, which
    // matches on them to build its spoken answer: changing one side means
    // changing the other.
    MoveWindowToMonitor(app, monitor) {
        const count = Main.layoutManager.monitors.length;
        if (monitor < 0 || monitor >= count) {
            return JSON.stringify({
                ok: false, error: `no monitor ${monitor} (there are ${count})`,
            });
        }
        const win = this._findWindow(app);
        if (!win)
            return JSON.stringify({ok: false, error: 'window not found'});

        win.move_to_monitor(monitor);
        return JSON.stringify({
            ok: true, title: win.get_title(), monitor,
        });
    }

    _place() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._soulConnector)
            return;
        // Never the raw monitor rectangle: if there is a dock (Ubuntu dock,
        // dash-to-dock) reserving a strip, the work area rectangle already has
        // it subtracted -- that way the soul-connector sits above the dock
        // instead of overlapping and covered by it (actually happened: with the
        // dock at the bottom, the song name ended up behind the dock).
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        const base = area || monitor;

        // If the user moved it by hand, honor that position -- but still
        // clamped to the work area, so a dock that appears or a monitor that
        // gets disconnected does not leave it off screen, with no way to grab
        // it and bring it back.
        if (this._position) {
            this._soulConnector.actor.set_position(
                Math.max(base.x, Math.min(this._position.x, base.x + base.width - WIDTH)),
                Math.max(base.y, Math.min(this._position.y, base.y + base.height - HEIGHT))
            );
            return;
        }

        this._soulConnector.actor.set_position(
            base.x + base.width - WIDTH - MARGIN,
            base.y + base.height - HEIGHT - MARGIN
        );
    }

    disable() {
        if (this._windowsDbus) {
            this._windowsDbus.flush();
            this._windowsDbus.unexport();
            this._windowsDbus = null;
        }
        if (this._monitorsId) {
            Main.layoutManager.disconnect(this._monitorsId);
            this._monitorsId = 0;
        }
        if (this._areasId) {
            global.display.disconnect(this._areasId);
            this._areasId = 0;
        }
        if (this._drag) {
            this._drag.destroy();
            this._drag = null;
        }
        if (this._soulConnector) {
            Main.layoutManager.removeChrome(this._soulConnector.actor);
            this._soulConnector.destroy();
            this._soulConnector = null;
        }
        if (this._panel) {
            this._panel.teardown();
            this._panel = null;
        }
    }
}
