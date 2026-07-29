plugins {
    id("com.android.application")
}

android {
    namespace = "com.webdevbar.oddsoverlay"
    compileSdk = 33

    defaultConfig {
        applicationId = "com.webdevbar.oddsoverlay"
        minSdk = 26
        // targetSdk 33 deliberately: API 34 requires every foreground service to declare
        // a foregroundServiceType, and none of them honestly describes this one.
        targetSdk = 33
        // Bump together with OVERLAY_VERSION in overlay.py, or an upgraded desktop app
        // will keep running last month's overlay.
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Debug signing so `adb install -r` upgrades in place during development.
            // CI re-signs with the release keystore.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// No dependencies and no Kotlin. The Kotlin build shipped a 2MB classes.dex because the
// standard library rides along, for a service that draws one TextView; plain Java against
// platform APIs is a few kilobytes. That matters because this installs automatically at
// the start of a collection run, and a slow install costs draft reads.
dependencies {
}
