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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.3.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.3.0.zip",
            checksum: "fb1c3e06e407e0774a63444decf186610641a469a97f5a2003cec053f56b770c"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.3.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.3.0.zip",
            checksum: "d259d502801c8a6075e982169d7e598852e24e9725d7273ac091ced8463ea9bf"
        ),
    ]
)
