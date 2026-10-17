import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// ---------------------------------------------------------------------------
// Release signing (M4: "signed release build process — keys never committed")
//
// The keystore and its passwords live ONLY in android/key.properties and the
// .keystore/.jks file itself; android/.gitignore excludes key.properties,
// **/*.keystore and **/*.jks, so no key material can enter the repository.
// Without that file (a fresh clone, CI) the build still compiles, signed
// with the debug key, and `flutter run --release` keeps working — but it is
// NOT a distributable build.
//
// One-time setup on the release machine:
//   keytool -genkeypair -v -keystore ~/keys/aditya-bionova-release.jks \
//       -keyalg RSA -keysize 2048 -validity 10000 -alias bionova
// then create android/key.properties:
//   storeFile=/absolute/path/aditya-bionova-release.jks
//   storePassword=…
//   keyAlias=bionova
//   keyPassword=…
// ---------------------------------------------------------------------------
val keystorePropertiesFile = rootProject.file("key.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        keystorePropertiesFile.inputStream().use { load(it) }
    }
}

android {
    namespace = "`in`.adityabionova.aditya_bionova_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "`in`.adityabionova.aditya_bionova_mobile"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://flutter.dev/to/review-gradle-config)
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (keystorePropertiesFile.exists()) {
            create("release") {
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = if (keystorePropertiesFile.exists()) {
                signingConfigs.getByName("release")
            } else {
                // Debug key only: local testing, never distribution.
                signingConfigs.getByName("debug")
            }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
