package org.yashome.panel;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** Kiosk settings. A page of its own because the launcher is the only one on the
 *  device: without it there is no way to Wi-Fi or to a pairing reset. */
public class SettingsActivity extends Activity implements View.OnClickListener {
    private static final int SAVE = 1, RESET = 2, PASSPORT = 3, RELOAD = 4, ANDROID = 5;
    private EditText name, url, token, panel;
    private TextView info;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout v = new LinearLayout(this);
        v.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        v.setPadding(pad, pad, pad, pad);
        v.setBackgroundColor(0xFF0E1116);

        v.addView(head(getString(R.string.settings_title)));
        name  = field(v, getString(R.string.set_device_name), Prefs.name(this));
        url   = field(v, getString(R.string.set_server), Prefs.url(this));
        token = field(v, getString(R.string.set_token), Prefs.token(this));
        panel = field(v, getString(R.string.set_panel), Prefs.panel(this));
        v.addView(button(getString(R.string.set_save), SAVE));
        v.addView(button(getString(R.string.set_reload), RELOAD));
        v.addView(button(getString(R.string.set_reset), RESET));
        v.addView(button(getString(R.string.set_passport), PASSPORT));
        v.addView(button(getString(R.string.set_android), ANDROID));

        info = new TextView(this);
        info.setTextColor(0xFF8A93A3);
        info.setTextSize(12);
        info.setPadding(0, pad, 0, 0);
        info.setText(getString(R.string.set_info, Prefs.deviceId(this), Prefs.code(this),
                               Caps.ip(), Caps.APP_VER));
        v.addView(info);

        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(0xFF0E1116);
        sv.addView(v);
        setContentView(sv);
    }

    private TextView head(String t) {
        TextView h = new TextView(this);
        h.setText(t);
        h.setTextColor(Color.WHITE);
        h.setTextSize(22);
        h.setPadding(0, 0, 0, 12);
        return h;
    }

    private EditText field(LinearLayout root, String label, String value) {
        TextView l = new TextView(this);
        l.setText(label);
        l.setTextColor(0xFF8A93A3);
        l.setTextSize(13);
        root.addView(l);
        EditText e = new EditText(this);
        e.setText(value);
        e.setTextColor(Color.WHITE);
        e.setSingleLine(true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        root.addView(e);
        return e;
    }

    private Button button(String t, int id) {
        Button b = new Button(this);
        b.setText(t);
        b.setId(id);
        b.setOnClickListener(this);
        return b;
    }

    @Override public void onClick(View v) {
        switch (v.getId()) {
            case SAVE:
                Prefs.setName(this, name.getText().toString().trim());
                Prefs.save(this, url.getText().toString().trim(),
                           token.getText().toString().trim(),
                           panel.getText().toString().trim());
                Launcher.forceReload = true;
                finish();
                break;
            case RELOAD:
                Launcher.forceReload = true;
                finish();
                break;
            case RESET:
                Prefs.reset(this);
                Launcher.forceReload = true;
                toast(getString(R.string.set_reset_done));
                finish();
                break;
            case PASSPORT:
                try {
                    info.setText(Caps.caps(this).toString(1));
                } catch (Exception e) {
                    info.setText(String.valueOf(e.getMessage()));
                }
                break;
            case ANDROID:
                startActivity(new Intent(Settings.ACTION_SETTINGS));
                break;
        }
    }

    private void toast(String t) { Toast.makeText(this, t, Toast.LENGTH_SHORT).show(); }
}
