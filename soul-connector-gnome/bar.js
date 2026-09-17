// A Cairo port of the progress bar in soul_connector/index.html (the
// pywebview soul-connector player), with one difference asked for on
// purpose: here it is **always white**. The original cycles the glow color
// every 4s and beats in red/green while paused; this version never changes
// hue, so the only thing that moves is the brightness.
//
// In CSS the glow came from `filter: drop-shadow()`. There are no filters
// here, so it is approximated with a vertical gradient that fades out
// upwards and downwards, in ADD mode (same as the wave: adding light is what
// keeps it from looking like plastic). Stacking solid ellipses, which was the
// first attempt, does not work: they add up in the center and what you see is
// a fat gray lens instead of a line with a glow. The animations come from the
// clock on every frame.
//
// The background bar (the "track") is deliberately not drawn: in the original
// it is transparent, and that long line crossing everything is really the
// wave axis, which this bar rests on.

import Cairo from 'cairo';

const BREATHE_CYCLE_MS = 1800;
const PULSE_CYCLE_MS = 2200;

// How far the glow reaches above and below the line, and how much it paints
// in the center. Equivalent to the two drop-shadows in the CSS.
const GLOW_RADIUS = 3;
const GLOW_ALPHA = 0.07;

const TIP_LAYERS = [[5, 0.14], [3, 0.22]];

// Below this the fill is not drawn: an ellipse of radius ~0 is a degenerate
// matrix, and Cairo cannot invert it.
const MIN_VISIBLE = 1;

/** 0..1 and back, smoothly: the equivalent of an alternating ease-in-out. */
function breathe(now, cycle) {
    return 0.5 - 0.5 * Math.cos((2 * Math.PI * (now % cycle)) / cycle);
}

function white(cr, alpha) {
    cr.setSourceRGBA(1, 1, 1, Math.min(1, alpha));
}

export class Bar {
    /**
     * @param cr the St.DrawingArea Cairo context
     * @param lineY the height the line goes at, within the drawing area
     * @param fraction 0..1 of the song already played
     * @param paused swaps the breathing for a more marked heartbeat
     */
    draw(cr, width, lineY, fraction, paused) {
        const now = Date.now();
        // The +0.5 is so a 1px line falls on a pixel and not between two
        // (where Cairo spreads it over two gray rows).
        const y = Math.round(lineY) + 0.5;
        const x = Math.max(0, Math.min(1, fraction)) * width;

        // Paused beats more markedly and more slowly; while playing it
        // barely breathes. That is the only difference between the two
        // states, since the color never changes.
        const beat = paused
            ? breathe(now, PULSE_CYCLE_MS)
            : breathe(now, BREATHE_CYCLE_MS) * 0.4;

        // The fill is drawn as an ellipse, not as a line. The original has
        // `border-radius: 100%`, which on a box 1px tall does not round
        // corners: it turns it into an ellipse that tapers away to nothing at
        // both tips. With a straight line, on the other hand, Cairo caps it
        // square and leaves an ugly vertical cut right at the start of the
        // bar.
        if (x > MIN_VISIBLE) {
            // The glow as a vertical gradient and not as stacked solid
            // ellipses: stacked, they add up in the center (0.10 + 0.16 +
            // 0.30 in ADD mode) and what you see is a fat gray lens, not a
            // line with a glow. The gradient fades out upwards and
            // downwards, which is what a real blur does.
            const radius = GLOW_RADIUS * (1 + 0.25 * beat);
            const gradient = new Cairo.LinearGradient(0, y - radius, 0, y + radius);
            gradient.addColorStopRGBA(0, 1, 1, 1, 0);
            gradient.addColorStopRGBA(0.5, 1, 1, 1, GLOW_ALPHA * (1 + 0.3 * beat));
            gradient.addColorStopRGBA(1, 1, 1, 1, 0);
            cr.setOperator(Cairo.Operator.ADD);
            cr.setSource(gradient);
            this._ellipse(cr, x / 2, y, x / 2, radius);
            cr.fill();
            cr.setOperator(Cairo.Operator.OVER);

            // The real fill of the bar (background: #ffffffa6).
            white(cr, 0.65);
            this._ellipse(cr, x / 2, y, x / 2, 0.5);
            cr.fill();
        }

        this._drawTip(cr, x, y, beat);
    }

    _drawTip(cr, x, y, beat) {
        cr.setOperator(Cairo.Operator.ADD);
        for (const [radius, alpha] of TIP_LAYERS) {
            white(cr, alpha * (1 + 0.5 * beat));
            this._ellipse(cr, x, y, radius, radius * 0.6);
            cr.fill();
        }
        cr.setOperator(Cairo.Operator.OVER);

        white(cr, 1);
        this._ellipse(cr, x, y, 2, 1);
        cr.fill();
    }

    _ellipse(cr, cx, cy, rx, ry) {
        // Cairo stores the path already transformed, so restoring the matrix
        // after building it does not deform it back.
        cr.save();
        cr.translate(cx, cy);
        cr.scale(rx, ry);
        cr.arc(0, 0, 1, 0, 2 * Math.PI);
        cr.restore();
    }
}
