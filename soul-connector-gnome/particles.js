// Particles that are born on the wave axis and rise while fading out -- the
// "energized" state seen when Tero delegates to Codex/ChatGPT (phase 4,
// `delegate_to_codex`). Asked for by the user, with a CSS demo as reference
// (full-screen particles rising with `@keyframes rise`): born at the bottom,
// quick fade-in, drifting a bit sideways, fade-out while rising. Here "up" is
// the tip of a 74px wave, not the whole screen, so the duration and the
// travel shrink.
//
// The long way round to get here: a first attempt with a radial gradient
// (bright center fading towards the edge, all in the same circle) looked like
// a shaded sphere -- that is human perception, not a matter of numbers: any
// brightness that depends on the distance to the center reads as 3D volume. A
// second attempt stacking rings of decreasing alpha to approximate a blur
// still showed the same problem under close inspection. The real solution: do
// not approximate the blur by hand at all. `Shell.BlurEffect` (the same
// effect gnome-shell uses for the overview background, real, on the GPU) is
// applied to a separate layer that draws the dots SMALL and FLAT (no
// gradient), and the blur of that whole layer acts as the halo -- exactly how
// `box-shadow` works in CSS: the crisp element on top, a blurred copy
// underneath. That is why this class exposes two separate drawing methods
// (`drawCores` for the crisp layer, `drawGlow` for the one carrying the
// `Shell.BlurEffect`) instead of a single `draw` with rings inside. The
// extension wiring (two `St.DrawingArea`, one with the effect) is in
// `extension.js`.
//
// Same pattern as `wave.js`/`bar.js`: every particle stores when it was born
// (`Date.now()`) and everything is recomputed from that mark, with no separate
// "update" tick -- except that here `update()` IS separate from the two
// `draw*`, because both need to see the SAME list of particles in the same
// state (if each one reproduced the spawn on its own, two different sets of
// particles would come out: one for the core, another for the glow).

import Cairo from 'cairo';

// The same accent as the demo: cyan, violet, pink, white.
const PALETTE = [
    [0x00, 0xea, 0xff],
    [0x7b, 0x61, 0xff],
    [0xff, 0x3c, 0xac],
    [0xff, 0xff, 0xff],
];

// Much more speed variation than the demo (there it was just 4-8s, a 1:2
// range) -- an explicit request from the user, more range keeps them from all
// rising "in a row" at once.
const DURATION_MS = [400, 2000];
const CORE_RADIUS_PX = [0.6, 1.4]; // crisp dot, the layer without blur
const GLOW_RADIUS_PX = [1.8, 3.6]; // flat dot on the layer WITH blur -- the
                                   // halo comes from Shell.BlurEffect, not
                                   // from this radius (small on purpose)
const CORE_ALPHA = [0.75, 1];
const DRIFT_X_PX = [-16, 16];
// How much higher than the wave axis they get to rise before dying. Tested
// live (nested shell + real session): with 18px they stayed inside the wave's
// own swing in "codex" (amplitude 0.55, which already takes ~14px on each
// side of the axis) -- they read as mixed in, not as something rising
// separately. With this they clearly break away above it, and still die well
// before the edge of the widget's 74px (the fade-out is already complete at
// progress=1, see opacity()).
const RISE_HEIGHT_PX = 27;

// High density: many at once, not a few big ones.
const SPAWN_INTERVAL_MS = 22;
const MAX_PARTICLES = 45;

function random([from, to]) {
    return from + Math.random() * (to - from);
}

function randomColor() {
    return PALETTE[Math.floor(Math.random() * PALETTE.length)];
}

/** 0..1 -> 0..1: quick fade-in (15% of the life), fade-out for the rest, the
 * same shape as the demo's keyframe (there it comes from interpolating
 * 0%→15%→100%). */
function opacity(progress) {
    if (progress < 0.15)
        return progress / 0.15;
    return 1 - (progress - 0.15) / 0.85;
}

export class Particles {
    constructor() {
        this._particles = [];
        this._lastSpawn = 0;
    }

    /** Ages them, prunes the dead ones and spawns a new one if due. Called
     * once per frame, before the two `draw*` -- see the comment above about
     * why it does not live inside each drawing method. */
    update(active, width) {
        const now = Date.now();
        if (active && this._particles.length < MAX_PARTICLES
            && now - this._lastSpawn >= SPAWN_INTERVAL_MS) {
            this._lastSpawn = now;
            const x = random([8, width - 8]);
            // 0 at the center of the widget, 1 at the edge -- quadratic so the
            // falloff is gentle near the center and more noticeable towards
            // the tips. The user's request: at the corners the particle barely
            // lifts off the line, at the center it rises as usual (the whole
            // RISE_HEIGHT_PX).
            const center = width / 2;
            const distance = Math.abs(x - center) / center;
            const heightFactor = Math.pow(1 - distance, 2);
            this._particles.push({
                bornAt: now,
                duration: random(DURATION_MS),
                x,
                heightFactor,
                driftX: random(DRIFT_X_PX),
                coreRadius: random(CORE_RADIUS_PX),
                glowRadius: random(GLOW_RADIUS_PX),
                coreAlpha: random(CORE_ALPHA),
                color: randomColor(),
            });
        }
        this._particles = this._particles.filter(
            p => (now - p.bornAt) / p.duration < 1);
    }

    _forEach(yBase, cb) {
        const now = Date.now();
        for (const p of this._particles) {
            const progress = (now - p.bornAt) / p.duration;
            const x = p.x + p.driftX * progress;
            const y = yBase - RISE_HEIGHT_PX * p.heightFactor * progress;
            const alpha = opacity(progress);
            // It grows a little while rising, same as the demo (scale 0.3 -> 1).
            const growth = 0.3 + 0.7 * progress;
            cb(p, x, y, alpha, growth);
        }
    }

    /** The crisp layer, no blur: the "spark" -- one small solid dot per
     * particle, in an even color. */
    drawCores(cr, yBase) {
        cr.setOperator(Cairo.Operator.ADD);
        this._forEach(yBase, (p, x, y, alpha, growth) => {
            const [r, g, b] = p.color;
            cr.setSourceRGBA(r / 255, g / 255, b / 255, alpha * p.coreAlpha);
            cr.arc(x, y, p.coreRadius * growth, 0, 2 * Math.PI);
            cr.fill();
        });
        cr.setOperator(Cairo.Operator.OVER);
    }

    /** The layer carrying the `Shell.BlurEffect` on top (see extension.js):
     * equally flat dots, no gradient -- the halo comes from blurring this
     * whole layer on the GPU, not from anything done in here. */
    drawGlow(cr, yBase) {
        cr.setOperator(Cairo.Operator.ADD);
        this._forEach(yBase, (p, x, y, alpha, growth) => {
            const [r, g, b] = p.color;
            cr.setSourceRGBA(r / 255, g / 255, b / 255, Math.min(1, alpha * 1.6));
            cr.arc(x, y, p.glowRadius * growth, 0, 2 * Math.PI);
            cr.fill();
        });
        cr.setOperator(Cairo.Operator.OVER);
    }
}
