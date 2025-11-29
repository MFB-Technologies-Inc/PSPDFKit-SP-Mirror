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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.2.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.2.0.zip",
            checksum: "efd9780acd7d14b342afcf99e77de567d73ced9d692703e821dcd535b547805e"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-26.2.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.2.0.zip",
            checksum: "94de18dc94ca2cfca99b4ef98312c25ba0f7db8536c6d19931079402cd09aa4b"
        ),
    ]
)
