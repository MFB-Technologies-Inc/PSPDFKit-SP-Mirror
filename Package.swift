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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.12.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-14.12.0.zip",
            checksum: "b73a356efaa5952392beb01ec8c4baa69a92d5f39ec0db5edce89e874590f1fa"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.12.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-14.12.0.zip",
            checksum: "790b92328be56c95290c1460c3980a3a91d02268ef9c2b54fd2dedd60623af5f"
        ),
    ]
)
