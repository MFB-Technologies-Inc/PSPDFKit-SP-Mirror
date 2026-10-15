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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-27.1.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-27.1.0.zip",
            checksum: "c40975122507b7391a166332d419f91744c23facdeed1cae6911ed05a4e5e2dd"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-27.1.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-27.1.0.zip",
            checksum: "d3eed3733e1d85c05058236548ec62013fa6f3293c4a60acf6cde86da2719776"
        ),
    ]
)
