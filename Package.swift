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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.8.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.8.0.zip",
            checksum: "ec28c2ee534192937c5af2d587acd2c1e33ea26f1eecf7c279e72fd5629e31ce"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.8.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.8.0.zip",
            checksum: "eefcbd0895e270ec859d3ad9a21fe15f0d3f16b70e833567d93eda48d5e6253f"
        ),
    ]
)
