import Foundation

// A packaging host only. Never opens or registers itself during build checks.
@main
struct CandidateHost {
    static func main() {
        print("Planner FSKit candidate: not registered, not accepted, and mounting disabled.")
    }
}
