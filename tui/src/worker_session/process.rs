use std::{
    io,
    process::{Child, Command},
    thread,
    time::{Duration, Instant},
};

#[cfg(windows)]
mod windows;

pub(super) struct ProcessTree {
    #[cfg(unix)]
    pgid: i32,
    #[cfg(windows)]
    job: windows::Job,
}

impl ProcessTree {
    pub fn spawn(command: &mut Command) -> io::Result<(Child, Self)> {
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            let child = command.process_group(0).spawn()?;
            let tree = Self {
                pgid: child.id() as i32,
            };
            Ok((child, tree))
        }
        #[cfg(windows)]
        {
            let (child, job) = windows::Job::spawn(command)?;
            Ok((child, Self { job }))
        }
    }

    pub fn terminate(&self, child: &mut Child) -> io::Result<()> {
        #[cfg(unix)]
        {
            // This PGID belongs only to the child created above, never the terminal.
            if unsafe { libc::killpg(self.pgid, libc::SIGKILL) } != 0 {
                let mut error = io::Error::last_os_error();
                // Darwin returns EPERM for a group containing only an unreaped zombie.
                // Reap our own dead child, then retry: never swallow a genuine permission error.
                if error.raw_os_error() == Some(libc::EPERM) && child.try_wait()?.is_some() {
                    if unsafe { libc::killpg(self.pgid, libc::SIGKILL) } == 0 {
                        return child.wait().map(|_| ());
                    }
                    error = io::Error::last_os_error();
                }
                if error.raw_os_error() != Some(libc::ESRCH) {
                    return Err(error);
                }
            }
        }
        #[cfg(windows)]
        self.job.terminate()?;
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if child.try_wait()?.is_some() {
                child.wait()?; // try_wait has reaped it; wait returns the stored status.
                #[cfg(windows)]
                self.job.wait_empty(deadline)?;
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "worker did not terminate",
                ));
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}
