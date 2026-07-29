package com.webdevbar.oddsoverlay;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.RelativeSizeSpan;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;

/**
 * A bubble showing the favoured side's win probability, driven entirely over adb.
 *
 * <p>A small rounded plate rather than a full-width bar, and the colour carries the
 * direction: blue means blue is favoured, so only one number is ever shown and the other
 * is implied. That is what lets it be small - about 115x61 against the full 1080 width -
 * which in turn keeps it clear of everything the automation reads off the same screen.
 *
 * <p>Nothing is drawn when there is no favourite. That is not only tidiness: inside 48-52%
 * the model is right 50.2% of the time across 705 predictions, a literal coin flip, so
 * "no favourite" and "nothing worth showing" are the same condition. The bubble appearing
 * at all is therefore the first half of the signal.
 *
 * <p>Java rather than Kotlin: the Kotlin build shipped a 2MB classes.dex because the
 * standard library rides along. This one is 4KB, which matters because it installs itself
 * at the start of a collection run.
 */
public class OverlayService extends Service {

    // Fractions of display height, so the bubble lands in the same place on any screen.
    // Never pixel constants - a hardcoded position measured on 1080x1920 would land in
    // the middle of the pool grid on a 2340-tall device.
    private static final float HEIGHT_FRACTION = 0.036f;  // ~69px on 1920
    private static final float WIDTH_FRACTION = 0.068f;   // ~131px on 1920
    private static final float CORNER_FRACTION = 0.008f;  // ~15px radius
    private static final float BOTTOM_MARGIN_FRACTION = 0.008f;
    private static final float TEXT_FRACTION = 0.023f;    // ~44px on 1920
    // The % is half height: it is a unit, not part of the number, and at full size it
    // competes with the digits for attention.
    private static final float PERCENT_SCALE = 0.78f;
    // Optical rather than geometric centring. "63%" centred by its bounding box reads as
    // sitting left, because the small % adds width without adding visual weight - so the
    // whole label is nudged right until it looks centred.
    private static final float OPTICAL_SHIFT_FRACTION = 0.0045f;
    private static final float GAP_FRACTION = 0.0012f;
    // The % is drawn taller than the plate on purpose - it is a watermark, so letting it
    // bleed past the edges reads as texture rather than as a cramped character.
    private static final float SIGN_SCALE = 1.3f;
    // Positive is down. Tuned by eye on device against a live game screen - there is no
    // formula for where a watermark sits right behind a number. Locked; do not "fix".
    private static final float SIGN_NUDGE_PX = 1f;
    private static final float SIGN_NUDGE_X_PX = -2f;
    private static final float NUMBER_DROP_PX = 5f;

    // Fully opaque. The game behind this is bright and busy, and a translucent plate
    // reads as part of the UI rather than as something to look at.
    private static final int BLUE = 0xFF1565E0;
    private static final int RED = 0xFFE01515;
    private static final String CHANNEL = "odds";
    private static final int NOTIFICATION_ID = 1;

    private FrameLayout view;
    private TextView label;
    private TextView sign;
    private WindowManager windows;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        windows = (WindowManager) getSystemService(WINDOW_SERVICE);
        startForeground(NOTIFICATION_ID, notification());
    }

    /**
     * Two extras and nothing else: {@code mode} is hidden, blue or red, and {@code pct}
     * is the favoured side's percentage.
     *
     * <p>Separate fields rather than one string on purpose. A single "blue 63" has to be
     * quoted to survive the device shell, and a stray quote then leaks into the rendered
     * text - which it did, as "63'%". Neither of these values can contain a space, so
     * neither needs quoting and neither can be mangled.
     */
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String mode = intent == null ? null : intent.getStringExtra("mode");
        if (mode == null || "hidden".equalsIgnoreCase(mode)) {
            detach();
            return START_NOT_STICKY;
        }
        int pct = intent.getIntExtra("pct", -1);
        if (pct < 0) {
            detach();
            return START_NOT_STICKY;
        }
        show(String.valueOf(pct), "red".equalsIgnoreCase(mode) ? RED : BLUE);
        // NOT START_STICKY: a restart with a null intent would repaint a stale number
        // with no way to know it is stale. The next pick sends another command anyway.
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        detach();
        super.onDestroy();
    }

    private void show(String percent, int colour) {
        // Checked rather than assumed: without the grant addView throws, and a crash loop
        // is a worse failure than no overlay.
        if (!Settings.canDrawOverlays(this)) {
            stopSelf();
            return;
        }
        int height = getResources().getDisplayMetrics().heightPixels;

        if (view != null) {
            label.setText(percent);
            place(height);
            ((GradientDrawable) view.getBackground()).setColor(colour);
            return;
        }

        // A rounded plate rather than a circle: a circle wastes its corners on a
        // three-character label, so the same text needs a much larger footprint - and
        // footprint is the whole cost of this overlay.
        GradientDrawable plate = new GradientDrawable();
        plate.setShape(GradientDrawable.RECTANGLE);
        plate.setCornerRadius(CORNER_FRACTION * height);
        plate.setColor(colour);

        // TWO views, not one string. The number is centred in the plate on its own, with
        // nothing to correct for - which is the only way it is exactly centred rather
        // than approximately. The % is a second view placed beside it.
        //
        // Everything else was tried and failed on device: padding narrows the box the
        // label centres in, so "100%" overflowed against the right edge; translating the
        // view that carried the background moved the plate and clipped its corners.
        label = digits(height);
        label.setText(percent);

        sign = new TextView(this);
        sign.setText("%");
        // Half opacity: the % is a unit, not part of the number, and at full weight it
        // competes with the digits for the glance this whole overlay exists to survive.
        sign.setTextColor(Color.WHITE);
        sign.setAlpha(0.18f);
        // Not monospace. The number is monospace so its width never changes between
        // values; the % is decoration behind it and reads better in the default face.
        sign.setTypeface(Typeface.DEFAULT_BOLD);
        // As tall as the plate and right-aligned: it is a watermark behind the number,
        // not a suffix after it.
        sign.setTextSize(TypedValue.COMPLEX_UNIT_PX, HEIGHT_FRACTION * height * SIGN_SCALE);
        sign.setGravity(Gravity.END | Gravity.CENTER_VERTICAL);
        sign.setIncludeFontPadding(false);

        FrameLayout container = new FrameLayout(this);
        container.setBackground(plate);
        // The % goes in FIRST, so it sits behind the number rather than beside it.
        //
        // Its view is DELIBERATELY taller than the plate. At MATCH_PARENT the glyph was
        // clipped by its own view rather than by the plate, so lifting the view carried
        // the cut upward with it and the % lost its bottom while empty plate sat below.
        // A taller view contains the whole glyph; the plate-sized window does the
        // clipping, which is what makes the bleed symmetrical.
        FrameLayout.LayoutParams tall = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                Math.round(HEIGHT_FRACTION * height * SIGN_SCALE * 2f));
        tall.gravity = Gravity.CENTER_VERTICAL;
        container.setClipChildren(false);
        container.addView(sign, tall);
        container.addView(label, matchParent());
        windows.addView(container, params(height));
        view = container;
        place(height);
    }

    /** The number: monospace so every digit is the same width and nothing shifts. */
    private TextView digits(int height) {
        TextView text = new TextView(this);
        text.setTextColor(Color.WHITE);
        text.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        text.setTextSize(TypedValue.COMPLEX_UNIT_PX, TEXT_FRACTION * height);
        text.setGravity(Gravity.CENTER);
        text.setPadding(0, 0, 0, 0);
        text.setIncludeFontPadding(false);
        // Bolder than the bold face. Monospace has no heavier weight available, so the
        // glyphs are stroked as well as filled - a fake bold, but the only way to add
        // weight without changing the typeface that keeps every value the same width.
        text.getPaint().setStyle(android.graphics.Paint.Style.FILL_AND_STROKE);
        text.getPaint().setStrokeWidth(2.2f);
        // A shadow, because the % now sits directly behind the digits: without it the
        // two whites run together where they overlap.
        text.setShadowLayer(5f, 0f, 2f, 0xB3000000);
        return text;
    }

    private FrameLayout.LayoutParams matchParent() {
        return new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT);
    }

    /**
     * Put the % immediately right of the centred number.
     *
     * <p>Both views fill the plate and centre their own text, so the % starts on the same
     * centre line and is pushed out by half the number's width plus a hair of space. The
     * number itself is never moved horizontally - it is centred because nothing touched
     * it.
     */
    private void place(int height) {
        // The % needs no horizontal placement now - its own gravity pins it to the
        // right edge of the plate, behind the number.
        sign.setTranslationX(SIGN_NUDGE_X_PX);
        // No vertical nudge on the number: with nothing padding or translating it, it is
        // centred by the layout, and that is the position to match rather than adjust.
        // The number is centred by the layout, then dropped by a fixed amount: digits sit
        // optically high inside their line box, so geometric centring reads as floating.
        float drop = NUMBER_DROP_PX;
        label.setTranslationY(drop);
        // Centred by its GLYPH, not its line box. A TextView centres the line box, which
        // includes ascent and descent sized for a font taller than the plate - so the %
        // rendered visibly low. Offsetting by the difference between the line box's
        // middle and the glyph's own middle puts the drawn shape on the centre line.
        android.graphics.Rect bounds = new android.graphics.Rect();
        sign.getPaint().getTextBounds("%", 0, 1, bounds);
        android.graphics.Paint.FontMetrics fm = sign.getPaint().getFontMetrics();
        float lineMiddle = (fm.ascent + fm.descent) / 2f;
        float glyphMiddle = (bounds.top + bounds.bottom) / 2f;
        // NOT offset by the number's drop. They were sharing it, so every nudge to the
        // number silently moved the watermark that had already been positioned.
        sign.setTranslationY((lineMiddle - glyphMiddle) + SIGN_NUDGE_PX);
    }

    /**
     * Remove the view entirely rather than blanking it.
     *
     * <p>The automation reads this same screen through screencap, and a detached window is
     * the only state provably identical to never having installed the overlay.
     */
    private void detach() {
        if (view != null) {
            windows.removeView(view);
            view = null;
            label = null;
            sign = null;
        }
    }

    private WindowManager.LayoutParams params(int height) {
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                Math.round(WIDTH_FRACTION * height),
                Math.round(HEIGHT_FRACTION * height),
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                // The default is OPAQUE, which would paint a black square around the
                // circle in every captured frame - the exact failure the position and the
                // detach-when-idle rule exist to avoid.
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        params.x = 0;
        params.y = Math.round(BOTTOM_MARGIN_FRACTION * height);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // Without this the origin is the app area rather than the display, and the
            // position means something different from what screencap returns.
            params.setFitInsetsTypes(0);
            params.layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        }
        return params;
    }

    private Notification notification() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL, "Odds overlay", NotificationManager.IMPORTANCE_MIN));
        }
        return new Notification.Builder(this, CHANNEL)
                .setContentTitle("Odds overlay")
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .build();
    }
}
