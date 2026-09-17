#!/usr/bin/env -S gjs -m
//
// Renders the soul-connector to a PNG, outside gnome-shell.
//
// Why it exists: GNOME caches the ES modules of extensions, so
// `disable`/`enable` does NOT reload the code (see README). On Wayland the
// only way to test a change live is to log out and back in -- one logout per
// iteration, blind, and on top of that GNOME blocks screenshots of the shell,
// so the one looking always has to be the user.
//
// `wave.js` and `bar.js` are pure Cairo and do not touch St or Clutter, which
// means they run perfectly in a plain gjs. This draws them onto an ImageSurface
// with the same geometry `extension.js` builds and writes a PNG, which can be
// looked at without spending a session.
//
//   ./preview.js [output.png]
//
// Mind what it does NOT test: on-screen positioning, the work area, the
// St.Labels (the times and the name are drawn here with Cairo separately, just
// to place the eye) and anything that depends on the shell. There is no
// shortcut for that.

import Cairo from 'cairo';
import GLib from 'gi://GLib';

import {Wave, baseline} from './wave.js';
import {Bar} from './bar.js';
import {Particles} from './particles.js';

const WIDTH = 260;
const HEIGHT = 74;
const BAR_HEIGHT = 20;
const SONG_Y = 39;

// The same colors as extension.js.
const COLORS = {
    idle: [0x4a, 0x44, 0x58],
    listening: [0xff, 0xff, 0xff],
    thinking: [0xc7, 0x7d, 0xff],
    music: [0x5c, 0xff, 0xd4],
    codex: [0x00, 0xea, 0xff],
};

// A desktop gray so something is visible: the real soul-connector goes over
// whatever is underneath, not over a background of its own.
const BACKGROUND = [0.12, 0.12, 0.13];

const SCALE = 3; // the PNG comes out enlarged, otherwise nothing is legible

function drawScene(cr, {state, amplitude, fraction, paused, song, particles}) {
    cr.setSourceRGB(...BACKGROUND);
    cr.paint();

    const color = COLORS[state] || COLORS.idle;
    const wave = new Wave();
    // The curves are born with amplitude 0 and ramp up slowly, so several
    // frames have to be run forward before there is anything to see. They go
    // to a throwaway surface: drawing them onto the good one adds them up in
    // ADD mode and everything comes out saturated white (it happened).
    const scratch = new Cairo.Context(
        new Cairo.ImageSurface(Cairo.Format.ARGB32, WIDTH, HEIGHT));
    for (let i = 0; i < 120; i++)
        wave.draw(scratch, WIDTH, HEIGHT, amplitude, 0.1, [color, color, color]);
    wave.draw(cr, WIDTH, HEIGHT, amplitude, 0.1, [color, color, color]);

    if (particles) {
        // CAREFUL: this is only good for checking density/positions/timings.
        // The real glow comes from a Shell.BlurEffect (GPU) that only exists
        // running inside gnome-shell -- there is no way to simulate it here, so
        // only the core layer is drawn, without blur. To see the full effect it
        // has to be looked at in test.sh or in the real session.
        //
        // update() uses the real Date.now(), so to see several born at
        // different moments (not a single freshly born one) real time has to
        // actually pass.
        const lineY = baseline(HEIGHT);
        const steps = 14;
        for (let i = 0; i < steps; i++) {
            particles.update(true, WIDTH);
            GLib.usleep(70000);
        }
        particles.update(true, WIDTH);
        particles.drawCores(cr, lineY);
    }

    if (fraction !== null) {
        const lineY = baseline(HEIGHT);
        cr.save();
        // The same horizontal inset as the St.BoxLayout: 10px of margin, 26px
        // of time and 6px of spacing on each side.
        cr.translate(42, lineY - BAR_HEIGHT / 2);
        new Bar().draw(cr, WIDTH - 84, BAR_HEIGHT / 2, fraction, paused);
        cr.restore();

        cr.selectFontFace('sans-serif', Cairo.FontSlant.NORMAL, Cairo.FontWeight.NORMAL);
        cr.setFontSize(9);
        cr.setSourceRGBA(1, 1, 1, 0.55);
        cr.moveTo(10, lineY + 3);
        cr.showText('0:46');
        cr.moveTo(WIDTH - 36, lineY + 3);
        cr.showText('3:29');

        cr.setFontSize(11);
        cr.setSourceRGBA(1, 1, 1, 0.8);
        const te = cr.textExtents(song);
        cr.moveTo((WIDTH - te.width) / 2, SONG_Y + 10);
        cr.showText(song);
    }
}

const SCENES = [
    {name: 'codex', state: 'codex', amplitude: 0.55, fraction: null,
     particles: new Particles()},
    {name: 'music', state: 'music', amplitude: 0.55, fraction: 0.42,
     paused: false, song: 'The Downfall of Us All · A Day To Remember'},
    {name: 'startup', state: 'music', amplitude: 0.55, fraction: 0.03,
     paused: false, song: 'Recién empezada: la punta no debe cortarse'},
    {name: 'paused', state: 'music', amplitude: 0.2, fraction: 0.42,
     paused: true, song: 'En pausa · late por brillo'},
    {name: 'listening', state: 'listening', amplitude: 0.7, fraction: null},
    {name: 'thinking', state: 'thinking', amplitude: 0.45, fraction: null},
    {name: 'idle', state: 'idle', amplitude: 0.15, fraction: null},
];

const output = ARGV[0] || 'preview.png';

// Every scene, one below the other, in a single image.
const surface = new Cairo.ImageSurface(
    Cairo.Format.ARGB32, WIDTH * SCALE, HEIGHT * SCENES.length * SCALE);
const cr = new Cairo.Context(surface);
cr.scale(SCALE, SCALE);

SCENES.forEach((scene, i) => {
    cr.save();
    cr.translate(0, i * HEIGHT);
    cr.rectangle(0, 0, WIDTH, HEIGHT);
    cr.clip();
    drawScene(cr, scene);
    cr.restore();
});

surface.writeToPNG(output);
surface.finish();
print(`escrito: ${output} (${SCENES.map(s => s.name).join(', ')})`);
GLib;
