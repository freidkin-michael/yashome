plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
// Firebase only with your project's google-services.json; without it the app pairs with the house alone
if (file("google-services.json").exists()) apply(plugin = "com.google.gms.google-services")

android {
    namespace = "com.yash.home"
    compileSdk = 34

    defaultConfig {
        // the Android app registered in your Firebase project: -PyashAppId=... (FAMILY_APP_ID)
        applicationId = (project.findProperty("yashAppId") as String?) ?: "com.yash.home"
        minSdk = 26
        targetSdk = 34
        versionCode = 55
        versionName = "0.38.0"
    }
    androidResources { generateLocaleConfig = false }

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
        // release.keystore in the app/ project root (keep it out of git); without it debug.keystore
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
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation(platform("com.google.firebase:firebase-bom:33.1.2"))
    implementation("com.google.firebase:firebase-messaging")
    implementation("com.google.firebase:firebase-auth")
    implementation("com.google.firebase:firebase-database")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.work:work-runtime-ktx:2.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.7.3")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
