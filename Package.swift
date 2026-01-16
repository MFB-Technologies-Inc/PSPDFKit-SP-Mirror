// swift-tools-version: 6.1

import PackageDescription

let package = Package(
    name: "PSPDFKit-SP-Mirror",
    platforms: [
        .iOS(.v16),
        .macCatalyst(.v16),
        .visionOS(.v1),
    ],
    products: [
        .library(
            name: "PSPDFKit",
            targets: ["PSPDFKit", "PSPDFKitUI"]
        )
    ],
    targets: [
        .binaryTarget(
            name: "PSPDFKit",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.4.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.4.0.zip",
            checksum: "44524b98f997c74edba441fa9680f0d61dae81ad7fc57ba814bee575b35838e0"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.4.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.4.0.zip",
            checksum: "d2b223cfb9abece3711e4a456d957a5cdc9215dce4b89e6817e379c04f31c00c"
        ),
    ]
)
