import FSKit

@main
struct PlannerCandidateExtension: UnaryFileSystemExtension {
    let fileSystem = CandidateFileSystem()
}
