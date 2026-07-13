class GigaamTui < Formula
  desc "Terminal UI for GigaAM speech transcription"
  homepage "https://github.com/dubr1k/GigaAMGUI"
  head "https://github.com/dubr1k/GigaAMGUI.git", branch: "main"

  depends_on "rust" => :build
  depends_on "python@3.12"
  depends_on "ffmpeg"

  def install
    system "cargo", "build", "--release", "--manifest-path", "tui/Cargo.toml"

    libexec.install "src", "requirements-tui.txt", "bin"
    libexec.install "tui/target/release/gigaam-tui"

    # The worker stays isolated from Homebrew's Python packages. It is installed
    # from the main repository, so TUI never needs a separately published binary.
    venv = libexec/"venv"
    system Formula["python@3.12"].opt_bin/"python3", "-m", "venv", venv
    system venv/"bin/pip", "install", "--upgrade", "pip"
    system venv/"bin/pip", "install", "-r", libexec/"requirements-tui.txt"

    (bin/"gigaam").write_env_script libexec/"gigaam-tui",
      GIGAAM_PROJECT_ROOT: libexec,
      GIGAAM_PYTHON: venv/"bin/python"
  end

  test do
    assert_predicate bin/"gigaam", :exist?
  end
end
