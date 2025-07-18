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
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.10.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-14.10.0.zip",
            checksum: "05d6ce9379141cac230ba464b9079d014ce5bc075921251c3fbe3915f9163d9d"
        ),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://github.com/MFB-Technologies-Inc/PSPDFKit-SP-Mirror/releases/download/pre-14.10.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-14.10.0.zip",
            checksum: "4b6f611af122e23a0c16e088e0ca24e73e0b8b096336c40a08343a6e8782f519"
        ),
    ]
)
