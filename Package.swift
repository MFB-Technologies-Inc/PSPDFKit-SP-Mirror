// swift-tools-version: 6.4

import PackageDescription

let package = Package(
    name: "PSPDFKit-SP-Mirror",
    platforms: [
        .iOS(.v17),
        .macCatalyst(.v17),
        .visionOS(.v1)
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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-27.2.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-27.2.0.zip",
            checksum: "df0238ef77a029be7ba01784df96ab1a004ff814cc4bfa7a9ac36ea9c65910d4"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-27.2.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-27.2.0.zip",
            checksum: "beb75a61bce23c9dfec0475e40c9ebb72c3a7ce9792daeb06afd3392a9ae4647"
        ),
    ]
)
