// A Cairo port of SiriWave's "ios9" style (soul_connector/siriwave.umd.js),
// which in the pywebview soul-connector was drawn on a <canvas>. The math is
// the same, translated 1:1 from iOS9Curve so the wave looks identical: each
// color "curve" is really a handful of overlapping sines that are born and
// die on their own, clipped by an envelope that fades them towards the
// edges.
//
// The two differences from the original, both on purpose:
//
// - The sampling step. SiriWave uses pixelDepth 0.02, i.e. 2500 points per
//   curve per side. At 260 px wide that is ~10 points per pixel: invisible,
//   and expensive here because this runs inside gnome-shell. With 0.1 (500
//   points) the stroke is indistinguishable.
// - The blending. The canvas used globalCompositeOperation "lighter"; the
//   Cairo equivalent is the ADD operator.

import Cairo from 'cairo';

const GRAPH_X = 25;
const AMPLITUDE_FACTOR = 0.8;
const ATTENUATION_FACTOR = 4;
const DESPAWN_FACTOR = 0.02;
const DEAD_PIXELS = 2;
const STEP = 0.1;
const ALPHA = 0.7;

const SUBCURVE_COUNT_RANGE = [2, 5];
const AMPLITUDE_RANGE = [0.3, 1];
const OFFSET_RANGE = [-3, 3];
const WIDTH_RANGE = [1, 3];
const SPEED_RANGE = [0.5, 1];
const DESPAWN_MS_RANGE = [500, 2000];

// The three colors the library ships with (blue, red, green). They are the
// ones used in the "speaking" state, which at the user's request is the only
// one that keeps the original multicolor; the rest paint the three curves in
// a flat color.
export const ORIGINAL_COLORS = [
    [15, 82, 169],
    [173, 57, 76],
    [48, 220, 155],
];

function random([from, to]) {
    return from + Math.random() * (to - from);
}

function attenuation(x) {
    return Math.pow(ATTENUATION_FACTOR / (ATTENUATION_FACTOR + x * x), ATTENUATION_FACTOR);
}

/** A color band: several sines that are born, live and fade on their own. */
class Curve {
    constructor() {
        this._bornAt = 0;
        this._previousMaxY = 0;
        this._subcurves = [];
    }

    _spawn() {
        this._bornAt = Date.now();
        const count = Math.floor(random(SUBCURVE_COUNT_RANGE));
        this._subcurves = [];
        for (let i = 0; i < count; i++) {
            this._subcurves.push({
                phase: 0,
                amplitude: 0,
                finalAmplitude: random(AMPLITUDE_RANGE),
                offset: random(OFFSET_RANGE),
                width: random(WIDTH_RANGE),
                speed: random(SPEED_RANGE),
                diesAt: random(DESPAWN_MS_RANGE),
                direction: random([-1, 1]),
            });
        }
    }

    _relativeY(i) {
        const total = this._subcurves.length;
        let y = 0;
        this._subcurves.forEach((sub, ci) => {
            // A fixed separation between subcurves, plus their own shift:
            // that is what keeps them from stacking on top of each other.
            const t = 4 * (-1 + (ci / (total - 1)) * 2) + sub.offset;
            const x = i / sub.width - t;
            y += Math.abs(
                sub.amplitude * Math.sin(sub.direction * x - sub.phase) * attenuation(x)
            );
        });
        return y / total;
    }

    _advance(globalSpeed) {
        const now = Date.now();
        for (const sub of this._subcurves) {
            const dying = this._bornAt + sub.diesAt <= now;
            sub.amplitude += dying ? -DESPAWN_FACTOR : DESPAWN_FACTOR;
            sub.amplitude = Math.min(Math.max(sub.amplitude, 0), sub.finalAmplitude);
            sub.phase = (sub.phase + globalSpeed * sub.speed) % (2 * Math.PI);
        }
    }

    draw(cr, width, maxHeight, globalAmplitude, globalSpeed, color) {
        if (this._bornAt === 0)
            this._spawn();
        this._advance(globalSpeed);

        const [r, g, b] = color;
        let maxY = -Infinity;

        // Two mirrored waves: the one above and the one below the axis.
        for (const sign of [1, -1]) {
            cr.newPath();
            for (let i = -GRAPH_X; i <= GRAPH_X; i += STEP) {
                const x = width * ((i + GRAPH_X) / (GRAPH_X * 2));
                const y =
                    AMPLITUDE_FACTOR *
                    maxHeight *
                    globalAmplitude *
                    this._relativeY(i) *
                    attenuation((i / GRAPH_X) * 2);
                cr.lineTo(x, maxHeight - sign * y);
                maxY = Math.max(maxY, y);
            }
            cr.closePath();
            cr.setSourceRGBA(r / 255, g / 255, b / 255, ALPHA);
            cr.fill();
        }

        // Once the band has faded out completely, it is born again with new
        // sines: which is why the wave never repeats exactly the same
        // drawing.
        if (maxY < DEAD_PIXELS && this._previousMaxY > maxY)
            this._bornAt = 0;
        this._previousMaxY = maxY;
    }
}

/**
 * Y of the wave axis, where the faint horizontal thread lives. The progress
 * bar rests exactly here: in the original the two read as a single
 * continuous line with the dot of light on top, so the offset has to come
 * from one place and not be repeated by hand.
 */
export function baseline(height) {
    return height / 2 - 6;
}

export class Wave {
    constructor() {
        this._curves = [new Curve(), new Curve(), new Curve()];
    }

    /**
     * @param cr the St.DrawingArea Cairo context
     * @param colors one [r,g,b] per curve (three)
     */
    draw(cr, width, height, amplitude, speed, colors) {
        const maxHeight = baseline(height);

        cr.setOperator(Cairo.Operator.OVER);
        this._drawBaseline(cr, width, maxHeight);

        // Additive: where two bands cross, the color adds up and lightens,
        // which is what gives it the look of light and not of plastic.
        cr.setOperator(Cairo.Operator.ADD);
        this._curves.forEach((curve, i) => {
            curve.draw(cr, width, maxHeight, amplitude, speed, colors[i]);
        });
        cr.setOperator(Cairo.Operator.OVER);
    }

    /** A faint horizontal thread on the axis, faded out at the tips. */
    _drawBaseline(cr, width, maxHeight) {
        const gradient = new Cairo.LinearGradient(0, maxHeight, width, 1);
        gradient.addColorStopRGBA(0, 1, 1, 1, 0);
        gradient.addColorStopRGBA(0.1, 1, 1, 1, 0.5);
        gradient.addColorStopRGBA(0.8, 1, 1, 1, 0.5);
        gradient.addColorStopRGBA(1, 1, 1, 1, 0);
        cr.setSource(gradient);
        cr.rectangle(0, maxHeight, width, 1);
        cr.fill();
    }
}
