//! The animated unicorn companion rendered through terminal image protocols.

use std::io::{self, Cursor, Write};

use image::ImageReader;
use ratatui_image::picker::ProtocolType;

use crate::{
    app::App,
    i18n::{t, tf},
};

const PET_IDLE_FRAMES: [&[u8]; 2] = [
    include_bytes!("../../assets/pets/unicorn-idle-01.png"),
    include_bytes!("../../assets/pets/unicorn-idle-02.png"),
];
const PET_RUN_FRAMES: [&[u8]; 3] = [
    include_bytes!("../../assets/pets/unicorn-run-01.png"),
    include_bytes!("../../assets/pets/unicorn-run-02.png"),
    include_bytes!("../../assets/pets/unicorn-run-03.png"),
];

impl App {
    pub(crate) fn clear_pet_layer(&self) {
        // Kitty images are persistent terminal layers and survive normal redraws.
        // Explicitly remove them on animation, resize, and when pets are disabled.
        if self.pet_protocol == Some(ProtocolType::Kitty) {
            let mut stdout = io::stdout();
            let _ = stdout.write_all(b"\x1b_Ga=d,d=A\x1b\\");
            let _ = stdout.flush();
        }
    }

    pub(crate) fn refresh_pet_image(&mut self) -> Result<(), String> {
        self.clear_pet_layer();
        let Some(picker) = self.pet_picker.as_ref() else {
            return Err(t(self.lang, "pets.unsupported").into());
        };
        let frame = if self.running {
            PET_RUN_FRAMES[self.pet_frame % PET_RUN_FRAMES.len()]
        } else {
            PET_IDLE_FRAMES[self.pet_frame % PET_IDLE_FRAMES.len()]
        };
        let image = ImageReader::new(Cursor::new(frame))
            .with_guessed_format()
            .map_err(|error| {
                tf(
                    self.lang,
                    "pets.image_error",
                    &[("error", &error.to_string())],
                )
            })?
            .decode()
            .map_err(|error| {
                tf(
                    self.lang,
                    "pets.image_error",
                    &[("error", &error.to_string())],
                )
            })?;
        self.pet_image = Some(picker.new_resize_protocol(image));
        Ok(())
    }
}
