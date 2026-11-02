package com.yash.home

import android.app.Application
import com.google.firebase.database.FirebaseDatabase

class YashApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Cloud.init(this)
        // Offline cache of /state; must be set before the first database call
        if (Cloud.enabled) FirebaseDatabase.getInstance().setPersistenceEnabled(true)
        Link.start(this)
        WidgetState.publish(this, force = false)
    }
}
