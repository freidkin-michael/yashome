plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.yashome.panel"
    compileSdk = 34

    defaultConfig {
        // a house with panels installed under another package: -PyashAppId=... (PANELS_APP_ID)
        applicationId = (project.findProperty("yashAppId") as String?) ?: "org.yashome.panel"
        minSdk = 24
        targetSdk = 34
        versionCode = 24
        versionName = "4.4"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    signingConfigs {
        getByName("debug") {
            storeFile = rootProject.file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
        // release.keystore in the project root (keep it out of git); without it debug.keystore
        // signs (switching keys later needs one reinstall).
        create("release") {
            storeFile = rootProject.file(System.getenv("ANDROID_KEYSTORE_FILE") ?: "release.keystore")
            storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
            keyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: "home"
            keyPassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (rootProject.file(System.getenv("ANDROID_KEYSTORE_FILE") ?: "release.keystore").exists())
                signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
}
