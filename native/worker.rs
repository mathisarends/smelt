//! Background children must not retain the hook runner's pipe handles.
use std::io;
use std::path::Path;

#[cfg(not(windows))]
pub fn spawn(python: &Path, endpoint: &Path, root: &Path) -> io::Result<std::process::Child> {
    use std::process::{Command, Stdio};
    Command::new(python)
        .args(["-m", "smelt.cli.session"])
        .arg(endpoint)
        .arg(root)
        .current_dir(
            endpoint
                .parent()
                .ok_or_else(|| io::Error::other("missing endpoint directory"))?,
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::System::Threading::{
        CREATE_NO_WINDOW, CreateProcessW, GetExitCodeProcess, PROCESS_INFORMATION, STARTUPINFOW,
        TerminateProcess, WaitForSingleObject,
    };

    pub struct Worker {
        handle: OwnedHandle,
        pid: u32,
    }

    impl Worker {
        pub fn id(&self) -> u32 {
            self.pid
        }

        pub fn try_wait(&mut self) -> io::Result<Option<u32>> {
            let mut code = 0;
            // SAFETY: the owned process handle is valid and code is writable.
            if unsafe { GetExitCodeProcess(self.handle.as_raw_handle(), &mut code) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok((code != 259).then_some(code)) // STILL_ACTIVE
        }

        pub fn kill(&mut self) -> io::Result<()> {
            // SAFETY: this handle belongs to the worker created below.
            if unsafe { TerminateProcess(self.handle.as_raw_handle(), 2) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }

        pub fn wait(&mut self) -> io::Result<()> {
            // SAFETY: the process handle remains owned for the bounded wait.
            if unsafe { WaitForSingleObject(self.handle.as_raw_handle(), 5000) } != 0 {
                return Err(io::Error::other("worker did not stop"));
            }
            Ok(())
        }
    }

    pub fn spawn(python: &Path, endpoint: &Path, root: &Path) -> io::Result<Worker> {
        let executable: Vec<u16> = python.as_os_str().encode_wide().chain([0]).collect();
        let directory: Vec<u16> = endpoint
            .parent()
            .ok_or_else(|| io::Error::other("missing endpoint directory"))?
            .as_os_str()
            .encode_wide()
            .chain([0])
            .collect();
        // Filesystem paths cannot contain quotes. Double trailing backslashes
        // before closing quotes, including a project at a drive root.
        fn quoted(path: &Path) -> String {
            let text = path.to_string_lossy();
            let trailing = text.chars().rev().take_while(|c| *c == '\\').count();
            format!("\"{}{}\"", text, "\\".repeat(trailing))
        }
        let mut arguments: Vec<u16> = format!(
            "{} -m smelt.cli.session {} {}",
            quoted(python),
            quoted(endpoint),
            quoted(root)
        )
        .encode_utf16()
        .chain([0])
        .collect();
        let startup = STARTUPINFOW {
            cb: std::mem::size_of::<STARTUPINFOW>() as u32,
            ..Default::default()
        };
        let mut process = PROCESS_INFORMATION::default();
        // SAFETY: buffers are NUL terminated and live for the call. Inheritance
        // is deliberately FALSE: std::process currently inherits unrelated
        // inheritable Windows handles even when all three stdio streams are null.
        let created = unsafe {
            CreateProcessW(
                executable.as_ptr(),
                arguments.as_mut_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                0,
                CREATE_NO_WINDOW,
                std::ptr::null(),
                directory.as_ptr(),
                &startup,
                &mut process,
            )
        };
        if created == 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: successful CreateProcessW transfers two valid, unique handles.
        let handle = unsafe { OwnedHandle::from_raw_handle(process.hProcess) };
        let thread = unsafe { OwnedHandle::from_raw_handle(process.hThread) };
        drop(thread);
        Ok(Worker {
            handle,
            pid: process.dwProcessId,
        })
    }
}

#[cfg(windows)]
pub use windows::spawn;
