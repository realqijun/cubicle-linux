import Foundation
import FSKit

final class CandidateFileSystem: FSUnaryFileSystem, FSUnaryFileSystemOperations {
    func probeResource(resource: FSResource, replyHandler reply: @escaping (FSProbeResult?, Error?) -> Void) {
        reply(nil, POSIXError(.ENOTCONN))
    }

    func loadResource(resource: FSResource, options: FSTaskOptions,
                      replyHandler reply: @escaping (FSVolume?, Error?) -> Void) {
        // Source paths and mount options cannot bootstrap authority. The future
        // broker must authenticate a reviewed worker/revision before loading.
        reply(nil, POSIXError(.ENOTCONN))
    }

    func unloadResource(resource: FSResource, options: FSTaskOptions,
                        replyHandler reply: @escaping (Error?) -> Void) {
        reply(nil)
    }
}
