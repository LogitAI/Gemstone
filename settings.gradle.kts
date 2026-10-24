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
            // `org.thisisthepy.compose.*:1.11.1-ext.1` POMs on Maven Central omit Gradle's
            // `published-with-gradle-metadata` marker comment, so Gradle must be told to read `.module`
            // metadata alongside `.pom` to resolve KMP platform variants and plugin runtime elements.
            metadataSources {
                gradleMetadata()
                mavenPom()
            }
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
            // `org.thisisthepy.compose.*:1.11.1-ext.1` POMs on Maven Central omit Gradle's
            // `published-with-gradle-metadata` marker comment, so Gradle must be told to read `.module`
            // metadata alongside `.pom` to resolve KMP platform variants and plugin runtime elements.
            metadataSources {
                gradleMetadata()
                mavenPom()
            }
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
