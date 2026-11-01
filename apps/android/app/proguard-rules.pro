# SilentNetwork ProGuard Rules
# ============================

# Keep domain models (data classes used for serialization/deserialization)
-keep class org.silentnetwork.app.model.** { *; }

# Keep Core bridge contract interfaces
-keep interface org.silentnetwork.app.core.** { *; }

# Keep Audio adapter contract interfaces
-keep interface org.silentnetwork.app.audio.** { *; }

# Keep Application class
-keep class org.silentnetwork.app.SilentApplication { *; }

# Future: When Rust Core FFI is integrated, keep JNI native methods
# -keepclasseswithmembernames class * {
#     native <methods>;
# }

# Remove verbose logging in release builds
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
}

# Keep ViewBinding generated classes
-keep class org.silentnetwork.app.databinding.** { *; }

# Standard Android optimizations
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile
