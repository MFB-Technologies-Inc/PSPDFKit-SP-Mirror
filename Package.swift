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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.8.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-14.8.0.zip",
            checksum: "15e36d61b47ee7aafa26ddfd83b734a7eb931d3a3986a0959dcc08c050ed56a3"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.8.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-14.8.0.zip",
            checksum: "d9d97b5c9509f65803f9d39c32c7c9a1217584068eaf31176daea7114ad02662"
        ),
    ]
)
