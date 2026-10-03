import Testing
@testable import GigaAMLiquidCore

@Suite struct WorkerRedactionTests {
    @Test func configuredSecretsAndCredentialShapesAreRedacted() {
        let text = "401 for key s3cr3t-value; HF hf_AbCdEf123; Authorization: Bearer abc.def; api_key=xyz; https://user:pass@host/x"
        let safe = WorkerRedaction.safeText(text, secrets: ["s3cr3t-value"])
        for leaked in ["s3cr3t-value", "hf_AbCdEf123", "abc.def", "xyz", "user:pass"] {
            #expect(!safe.contains(leaked), "\(leaked) leaked")
        }
        #expect(safe.hasPrefix("401 for key [redacted]"))
    }

    @Test func diagnosticsKeepTheirLastEightKilobytes() {
        let safe = WorkerRedaction.safeText(String(repeating: "a", count: 10_000) + "END", secrets: [])
        #expect(safe.count == WorkerRedaction.limit)
        #expect(safe.hasSuffix("END"))
    }

    @Test func emptySecretIsIgnored() {
        #expect(WorkerRedaction.safeText("plain", secrets: [""]) == "plain")
    }

    @Test func credentialLookingEnvironmentValuesAreCollected() {
        let secrets = WorkerRedaction.secrets(in: ["HF_TOKEN": "hf_123456", "OPENAI_API_KEY": "sk-abcdef", "PATH": "/usr/bin", "SHORT_TOKEN": "abc"])
        #expect(Set(secrets) == ["hf_123456", "sk-abcdef"])
    }
}
