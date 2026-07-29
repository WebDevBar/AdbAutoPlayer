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
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.WindowManager;
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

    // Fully opaque. The game behind this is bright and busy, and a translucent plate
    // reads as part of the UI rather than as something to look at.
    private static final int BLUE = 0xFF1565E0;
    private static final int RED = 0xFFE01515;
    private static final String CHANNEL = "odds";
    private static final int NOTIFICATION_ID = 1;

    private TextView view;
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
        String label = percent.endsWith("%") ? percent : percent + "%";

        if (view != null) {
            view.setText(label);
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

        TextView bubble = new TextView(this);
        bubble.setText(label);
        bubble.setTextColor(Color.WHITE);
        bubble.setTypeface(Typeface.DEFAULT_BOLD);
        bubble.setBackground(plate);
        bubble.setTextSize(TypedValue.COMPLEX_UNIT_PX, TEXT_FRACTION * height);
        bubble.setGravity(Gravity.CENTER);
        // The plate is a fixed-size window, not padding around the text, so a larger
        // font fills it rather than growing it. Padding is zeroed so the glyphs get the
        // whole plate and the size stays locked.
        bubble.setPadding(0, 0, 0, 0);
        bubble.setIncludeFontPadding(false);
        windows.addView(bubble, params(height));
        view = bubble;
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
