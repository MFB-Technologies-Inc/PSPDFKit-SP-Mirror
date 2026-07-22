// swift-tools-version: 6.3

import PackageDescription

let package = Package(
    name: "PSPDFKit-SP-Mirror",
    platforms: [
        .iOS(.v17),
        .macCatalyst(.v17),
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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.11.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.11.0.zip",
            checksum: "672218cfb02b615b89ccdc640aaa957743b8fcb0358e57b5bc136df0c6133f01"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.11.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.11.0.zip",
            checksum: "6c1ffa9d4e79cccf821bfefa2d166da2841e8231f4751876c614a925547e8b4f"
        ),
    ]
)
