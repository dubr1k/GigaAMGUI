mod asr;
mod llm;
mod live;
mod media_import;

fn main() {
    tauri::Builder::default()
        .manage(asr::AsrState::default())
        .manage(llm::LlmState::default())
        .manage(live::LiveState::default())
        .manage(media_import::MediaImportState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            asr::list_models,
            asr::transcribe,
            asr::cancel_transcription,
            llm::llm_process,
            llm::llm_cancel,
            llm::llm_tools,
            live::live_start,
            live::live_stop,
            live::live_cancel,
            media_import::import_media,
            media_import::cancel_media_import,
            media_import::release_media_import,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run GigaAMGUI desktop application");
}
