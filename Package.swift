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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.0.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.0.0.zip",
            checksum: "222dbb1dd4f633794f8f5499bbce939abfb38590745dacaba0ab6139995a3f20"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.0.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.0.0.zip",
            checksum: "5de249ef6c4b87437b17e0e76c7fc9f9d0eda3d5c86ae3b3d8d14f09e3c6e52e"
        ),
    ]
)
