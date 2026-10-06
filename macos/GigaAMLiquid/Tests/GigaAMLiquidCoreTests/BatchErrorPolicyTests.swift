import Testing
@testable import GigaAMLiquidCore

@Suite struct BatchErrorPolicyTests {
    /// The batch is `start`: its errors (rejected settings, a failed run) end the job.
    @Test func startErrorsFailTheBatch() {
        #expect(BatchErrorPolicy.disposition(command: "start", helloReply: false) == .fatal)
        #expect(BatchErrorPolicy.disposition(command: "start", helloReply: true) == .fatal)
    }

    /// A late reply to `cancel` ("Nothing is being processed") or any other
    /// command's error is not the batch failing.
    @Test func otherCommandsDoNotFailTheBatch() {
        #expect(BatchErrorPolicy.disposition(command: "cancel", helloReply: false) == .logged)
        #expect(BatchErrorPolicy.disposition(command: "resolve_inputs", helloReply: false) == .logged)
        #expect(BatchErrorPolicy.disposition(command: "hello", helloReply: true) == .diagnostic)
    }

    /// Without `command` (an older worker) the first reply after hello answers
    /// hello; anything else can only be the batch.
    @Test func olderWorkersAreReadByPosition() {
        #expect(BatchErrorPolicy.disposition(command: nil, helloReply: true) == .diagnostic)
        #expect(BatchErrorPolicy.disposition(command: nil, helloReply: false) == .fatal)
    }
}
