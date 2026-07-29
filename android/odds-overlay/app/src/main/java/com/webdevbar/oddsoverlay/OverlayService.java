package com.webdevbar.oddsoverlay;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.TextView;

/**
 * One strip of text at the bottom of the screen, driven entirely over adb.
 *
 * <p>Java rather than Kotlin, deliberately: the Kotlin build shipped a 2MB classes.dex
 * because the standard library rides along, for a service that draws one TextView. Plain
 * Java against platform APIs produces a dex of a few kilobytes, which installs in a
 * second instead of stalling - and this gets installed automatically at the start of a
 * collection run, where a slow install costs draft reads.
 *
 * <p>Every update arrives as a service start rather than a broadcast, so the same command
 * starts this service when it is dead and updates it when it is alive. A broadcast
 * reaches a runtime receiver only while its process lives, and so cannot revive a
 * service the system has killed.
 */
public class OverlayService extends Service {

    // Fractions of display height, measured on 1080x1920: y=1866, height=54, text=40.
    // Never pixel constants - nobody knows what a collaborator's emulator reports, and a
    // hardcoded 1866 on a 2340-tall screen lands in the middle of the pool grid, which is
    // exactly what this position exists to avoid.
    private static final float Y_FRACTION = 0.9719f;
    private static final float HEIGHT_FRACTION = 0.0281f;
    private static final float TEXT_FRACTION = 0.0208f;
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

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String text = intent == null ? "" : intent.getStringExtra("text");
        if (text == null || text.isEmpty()) {
            detach();
        } else {
            show(text);
        }
        // NOT START_STICKY: a restart with a null intent would repaint a stale number
        // with no way to know it is stale. The next pick sends another command anyway.
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        detach();
        super.onDestroy();
    }

    /** Paint, attaching the window on first use. */
    private void show(String text) {
        // Checked rather than assumed: without the grant addView throws, and a crash loop
        // is a worse failure than no overlay.
        if (!Settings.canDrawOverlays(this)) {
            stopSelf();
            return;
        }
        if (view != null) {
            view.setText(text);
            return;
        }
        int height = getResources().getDisplayMetrics().heightPixels;
        TextView strip = new TextView(this);
        strip.setText(text);
        strip.setTextColor(Color.WHITE);
        strip.setBackgroundColor(Color.TRANSPARENT);
        strip.setTextSize(TypedValue.COMPLEX_UNIT_PX, TEXT_FRACTION * height);
        strip.setGravity(Gravity.CENTER);
        // The game behind this is bright and varied; white text alone disappears over
        // pale UI. A shadow costs nothing and makes it readable over anything.
        strip.setShadowLayer(6f, 0f, 0f, Color.BLACK);
        windows.addView(strip, params(height));
        view = strip;
    }

    /**
     * Remove the view entirely rather than setting empty text.
     *
     * <p>A translucent attached surface should be invisible, but "should be" is not good
     * enough here: the bot reads this same screen through screencap, and a detached
     * window is the only state provably identical to never having installed the overlay.
     */
    private void detach() {
        if (view != null) {
            windows.removeView(view);
            view = null;
        }
    }

    private WindowManager.LayoutParams params(int height) {
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                Math.round(HEIGHT_FRACTION * height),
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                // The default is OPAQUE, which would paint a black bar across the bottom
                // of every captured frame even with no text - the exact failure this
                // position was chosen to avoid.
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.TOP | Gravity.START;
        params.x = 0;
        params.y = Math.round(Y_FRACTION * height);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // Without this the origin is the top of the APP area rather than the display,
            // and y means something different from what screencap returns.
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
