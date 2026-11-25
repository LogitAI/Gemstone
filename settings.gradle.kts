rootProject.name = "Gemstone"
enableFeaturePreview("TYPESAFE_PROJECT_ACCESSORS")

pluginManagement {
    repositories {
        google {
            mavenContent {
                includeGroupAndSubgroups("androidx")
                includeGroupAndSubgroups("com.android")
                includeGroupAndSubgroups("com.google")
            }
        }
        mavenCentral {
            // `org.thisisthepy.compose.*:1.11.1-ext.2` POMs on Maven Central (ext.5 carries the marker) omit Gradle's
            // `published-with-gradle-metadata` marker comment, so Gradle must be told to read `.module`
            // metadata alongside `.pom` to resolve KMP platform variants and plugin runtime elements.
            metadataSources {
                gradleMetadata()
                mavenPom()
            }
        }
        // feat/42-android-jit only (leader Decision 5, 2026-11-25): toolchain's Gradle plugin and packpack are
        // unpublished, so they resolve from mavenLocal (`./gradlew :toolchain:publishToMavenLocal` in toolchain).
        // Nothing else may come from there. This branch merges when toolchain 1.0.0 and PMP 3.15.0 are on Maven
        // Central, and this block goes then (owner, 2026-11-22: "배포된 아티팩트로").
        mavenLocal {
            content { includeGroupAndSubgroups("org.thisisthepy.python") }
        }
        gradlePluginPortal()
        maven {
            url = uri("https://maven.pkg.jetbrains.space/public/p/compose/dev")
        }
    }
}

dependencyResolutionManagement {
    repositories {
        google {
            mavenContent {
                includeGroupAndSubgroups("androidx")
                includeGroupAndSubgroups("com.android")
                includeGroupAndSubgroups("com.google")
            }
        }
        mavenCentral {
            // `org.thisisthepy.compose.*:1.11.1-ext.2` POMs on Maven Central (ext.5 carries the marker) omit Gradle's
            // `published-with-gradle-metadata` marker comment, so Gradle must be told to read `.module`
            // metadata alongside `.pom` to resolve KMP platform variants and plugin runtime elements.
            metadataSources {
                gradleMetadata()
                mavenPom()
            }
        }
        // feat/42-android-jit only (leader Decision 5, 2026-11-25): toolchain's Gradle plugin and packpack are
        // unpublished, so they resolve from mavenLocal (`./gradlew :toolchain:publishToMavenLocal` in toolchain).
        // Nothing else may come from there. This branch merges when toolchain 1.0.0 and PMP 3.15.0 are on Maven
        // Central, and this block goes then (owner, 2026-11-22: "배포된 아티팩트로").
        mavenLocal {
            content { includeGroupAndSubgroups("org.thisisthepy.python") }
        }
        maven {
            url = uri("https://jogamp.org/deployment/maven")
            url = uri("https://packages.jetbrains.team/maven/p/kpm/public/")
            url = uri("https://packages.jetbrains.team/maven/p/ij/intellij-dependencies/")
        }
    }
}

plugins {
    id("org.gradle.toolchains.foojay-resolver-convention").version("1.0.0")
}

include(":app")
