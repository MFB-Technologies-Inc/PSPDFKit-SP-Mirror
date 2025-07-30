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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.11.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-14.11.0.zip",
            checksum: "004c8b959dc7c1536ef9b8b17d1d7f9ec7e123be043cbb56249d22e89e379d07"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.11.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-14.11.0.zip",
            checksum: "4e214bce41fceeb2da88623dc0450c98aa2df3040a4173950c8301096298b765"
        ),
    ]
)
