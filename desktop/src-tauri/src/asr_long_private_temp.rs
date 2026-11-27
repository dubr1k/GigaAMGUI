//! A private, automatically cleaned temporary directory for local scratch data.
//!
//! On Unix the directory is created with mode 0700, subject to a more restrictive
//! process umask. On Windows it is created with a protected DACL granting only
//! the effective user's SID full control, including inheritable access for files
//! and subdirectories. Windows creation fails closed if the volume does not
//! advertise persistent ACLs or if the resulting owner/DACL is not as requested.
//!
//! The parent must be an absolute, trusted directory whose path and ancestors
//! cannot be replaced by another user or a temporary-file cleaner while this
//! directory is in use. This path-based API cannot defend against such replacement,
//! a privileged administrator, another process running as the same user, or a
//! caller explicitly giving a child broader permissions. Drop cleanup is best
//! effort and does not run after aborts or forced termination; call `close` to
//! observe cleanup errors. Do not use this for securely erasing data.
//!
//! Windows needs `windows-sys = 0.61` with `Win32_Foundation`, `Win32_Security`,
//! `Win32_Security_Authorization`, `Win32_Storage_FileSystem`,
//! `Win32_System_SystemServices`, and `Win32_System_Threading` features. The
//! crate also needs `tempfile` 3.27 or newer.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// A scratch directory whose path remains private until cleanup, subject to the
/// parent-directory and filesystem assumptions in the module documentation.
#[derive(Debug)]
pub struct PrivateTempDir {
    path: PathBuf,
    active: bool,
    // Denying FILE_SHARE_DELETE prevents the directory itself from being renamed
    // or deleted while in use. Release the handle before deleting its contents.
    #[cfg(windows)]
    handle: Option<std::os::windows::io::OwnedHandle>,
}

impl PrivateTempDir {
    /// Create a directory below the operating system's temporary directory.
    pub fn new() -> io::Result<Self> {
        Self::new_in(std::env::temp_dir())
    }

    /// Create a directory below an absolute, trusted parent directory.
    pub fn new_in(parent: impl AsRef<Path>) -> io::Result<Self> {
        let parent = parent.as_ref();
        if !parent.is_absolute() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "private temporary directory parent must be absolute",
            ));
        }

        // Builder supplies random names and retries collisions. The closure must
        // create the directory exclusively; it never opens an existing path.
        let named = tempfile::Builder::new()
            .prefix("gigaam-private-")
            .rand_bytes(16)
            .make_in(parent, create_at)?;
        let (directory, mut temporary_path) = named.into_parts();
        // The closure's directory guard owns cleanup. TempPath would otherwise
        // try to unlink the directory as a file when it is dropped.
        temporary_path.disable_cleanup(true);
        Ok(directory)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Remove the directory and its contents, returning any cleanup error.
    /// Open child files or directories must be closed first, especially on Windows.
    pub fn close(mut self) -> io::Result<()> {
        let result = self.remove();
        if result.is_ok() {
            self.active = false;
        }
        result
    }

    fn remove(&mut self) -> io::Result<()> {
        #[cfg(windows)]
        drop(self.handle.take());
        match fs::remove_dir_all(&self.path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            result => result,
        }
    }
}

impl Drop for PrivateTempDir {
    fn drop(&mut self) {
        if self.active {
            let _ = self.remove();
        }
    }
}

#[cfg(unix)]
fn create_at(path: &Path) -> io::Result<PrivateTempDir> {
    use std::os::unix::fs::DirBuilderExt;

    // Allocate before mkdir so that a successful creation immediately has a
    // cleanup guard. mkdir's mode is applied at creation, not via later chmod.
    let path = path.to_path_buf();
    fs::DirBuilder::new().mode(0o700).create(&path)?;
    Ok(PrivateTempDir { path, active: true })
}

#[cfg(windows)]
fn create_at(path: &Path) -> io::Result<PrivateTempDir> {
    windows::create_at(path)
}

#[cfg(not(any(unix, windows)))]
fn create_at(_path: &Path) -> io::Result<PrivateTempDir> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "private temporary directories are unsupported on this platform",
    ))
}

#[cfg(windows)]
mod windows {
    use super::PrivateTempDir;
    use std::ffi::c_void;
    use std::io;
    use std::mem::{size_of, zeroed};
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::path::Path;
    use std::ptr::{addr_of, null, null_mut};
    use windows_sys::Win32::Foundation::{
        LocalFree, ERROR_NO_TOKEN, HANDLE, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Security::Authorization::{GetSecurityInfo, SE_FILE_OBJECT};
    use windows_sys::Win32::Security::{
        AddAccessAllowedAceEx, EqualSid, GetAce, GetSecurityDescriptorControl,
        GetTokenInformation, InitializeAcl, InitializeSecurityDescriptor, IsValidSid,
        SetSecurityDescriptorControl, SetSecurityDescriptorDacl, SetSecurityDescriptorOwner,
        TokenUser, ACCESS_ALLOWED_ACE, ACL, ACL_REVISION, CONTAINER_INHERIT_ACE,
        DACL_SECURITY_INFORMATION, OBJECT_INHERIT_ACE, OWNER_SECURITY_INFORMATION, PSID,
        SECURITY_ATTRIBUTES, SECURITY_DESCRIPTOR, SECURITY_MAX_SID_SIZE, SE_DACL_PROTECTED,
        TOKEN_QUERY, TOKEN_USER,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateDirectoryW, CreateFileW, GetFileInformationByHandle, GetVolumeInformationByHandleW,
        BY_HANDLE_FILE_INFORMATION, FILE_ALL_ACCESS, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING, READ_CONTROL,
    };
    use windows_sys::Win32::System::SystemServices::{
        ACCESS_ALLOWED_ACE_TYPE, FILE_PERSISTENT_ACLS, SECURITY_DESCRIPTOR_REVISION,
    };
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, GetCurrentThread, OpenProcessToken, OpenThreadToken,
    };

    const USER_INFO_SIZE: usize = size_of::<TOKEN_USER>() + SECURITY_MAX_SID_SIZE as usize;
    const ACL_SIZE: usize = size_of::<ACL>() + size_of::<ACCESS_ALLOWED_ACE>()
        - size_of::<u32>() + SECURITY_MAX_SID_SIZE as usize;

    // TOKEN_USER contains a pointer and ACL/ACE fields require DWORD alignment.
    #[repr(C, align(8))]
    struct TokenUserBuffer([u8; USER_INFO_SIZE]);
    #[repr(C, align(8))]
    struct AclBuffer([u8; ACL_SIZE]);

    struct LocalDescriptor(*mut c_void);

    impl Drop for LocalDescriptor {
        fn drop(&mut self) {
            // SAFETY: GetSecurityInfo allocates this descriptor with LocalAlloc.
            unsafe { LocalFree(self.0) };
        }
    }

    fn wide_path(path: &Path) -> io::Result<Vec<u16>> {
        let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
        if wide.contains(&0) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"));
        }
        wide.push(0);
        Ok(wide)
    }

    fn effective_user_sid(storage: &mut TokenUserBuffer) -> io::Result<PSID> {
        let mut token: HANDLE = null_mut();
        // SAFETY: the pseudo handles are valid and token points to writable storage.
        let thread_result = unsafe { OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, 1, &mut token) };
        if thread_result == 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(ERROR_NO_TOKEN as i32) {
                return Err(error);
            }
            // Only fall back to the process token when there is no impersonation token.
            if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
                return Err(io::Error::last_os_error());
            }
        }
        // SAFETY: OpenThreadToken/OpenProcessToken returned a new owned handle.
        let token = unsafe { OwnedHandle::from_raw_handle(token) };
        let mut written = 0;
        // SAFETY: storage is aligned, writable, and large enough for TOKEN_USER
        // and the largest documented SID. It remains in place while SID is used.
        if unsafe {
            GetTokenInformation(
                token.as_raw_handle(),
                TokenUser,
                storage.0.as_mut_ptr().cast(),
                storage.0.len() as u32,
                &mut written,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        if (written as usize) < size_of::<TOKEN_USER>() {
            return Err(invalid_security("short token user information"));
        }
        // SAFETY: the successful call initialized an aligned TOKEN_USER.
        let sid = unsafe { (*(storage.0.as_ptr().cast::<TOKEN_USER>())).User.Sid };
        if sid.is_null() || unsafe { IsValidSid(sid) } == 0 {
            return Err(invalid_security("invalid effective user SID"));
        }
        Ok(sid)
    }

    fn invalid_security(message: &'static str) -> io::Error {
        io::Error::new(io::ErrorKind::PermissionDenied, message)
    }

    fn open_directory(path: &Path) -> io::Result<OwnedHandle> {
        let wide = wide_path(path)?;
        // SAFETY: the path is NUL-terminated. No security attributes or template
        // are needed to open an existing directory. The handle is not inheritable.
        let handle = unsafe {
            CreateFileW(
                wide.as_ptr(),
                READ_CONTROL | FILE_READ_ATTRIBUTES,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                null(),
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: CreateFileW returned a new owned handle.
        Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
    }

    fn verify_volume_and_type(handle: &OwnedHandle) -> io::Result<()> {
        // SAFETY: the output structure is writable and zero is a valid initial value.
        let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
        if unsafe { GetFileInformationByHandle(handle.as_raw_handle(), &mut info) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0
            || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        {
            return Err(invalid_security("temporary path is not a plain directory"));
        }
        let mut flags = 0;
        // SAFETY: optional buffers are null with zero lengths; flags is writable.
        if unsafe {
            GetVolumeInformationByHandleW(
                handle.as_raw_handle(), null_mut(), 0, null_mut(), null_mut(),
                &mut flags, null_mut(), 0,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        require_persistent_acls(flags)
    }

    fn require_persistent_acls(flags: u32) -> io::Result<()> {
        if flags & FILE_PERSISTENT_ACLS == 0 {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "temporary directory volume does not support persistent ACLs",
            ));
        }
        Ok(())
    }

    fn verify_owner_and_dacl(handle: &OwnedHandle, user_sid: PSID) -> io::Result<()> {
        let mut owner = null_mut();
        let mut dacl: *mut ACL = null_mut();
        let mut descriptor = null_mut();
        // SAFETY: the handle is open with READ_CONTROL; output pointers are valid.
        let result = unsafe {
            GetSecurityInfo(
                handle.as_raw_handle(), SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner, null_mut(), &mut dacl, null_mut(), &mut descriptor,
            )
        };
        if result != 0 {
            return Err(io::Error::from_raw_os_error(result as i32));
        }
        let descriptor = LocalDescriptor(descriptor);
        if descriptor.0.is_null() || owner.is_null() || dacl.is_null() {
            return Err(invalid_security("missing directory owner or DACL"));
        }
        // SAFETY: owner and DACL point into the descriptor returned by Windows;
        // user_sid points into the still-live token information buffer.
        if unsafe { EqualSid(owner, user_sid) } == 0 || unsafe { (*dacl).AceCount } != 1 {
            return Err(invalid_security("unexpected directory owner or DACL"));
        }
        let mut control = 0;
        let mut revision = 0;
        if unsafe { GetSecurityDescriptorControl(descriptor.0, &mut control, &mut revision) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if control & SE_DACL_PROTECTED == 0 {
            return Err(invalid_security("directory DACL is not protected from inheritance"));
        }
        let mut ace = null_mut();
        if unsafe { GetAce(dacl, 0, &mut ace) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if ace.is_null() {
            return Err(invalid_security("directory DACL has an invalid ACE"));
        }
        // SAFETY: GetAce returned the sole ACE in the system-provided ACL. Only
        // read the SID after checking that this is an access-allowed ACE.
        let allowed = unsafe { &*(ace.cast::<ACCESS_ALLOWED_ACE>()) };
        if u32::from(allowed.Header.AceType) != ACCESS_ALLOWED_ACE_TYPE
            || u32::from(allowed.Header.AceFlags) != OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
            || allowed.Mask != FILE_ALL_ACCESS
        {
            return Err(invalid_security("directory DACL is not user-only inheritable full access"));
        }
        let ace_sid = addr_of!(allowed.SidStart).cast_mut().cast();
        if unsafe { EqualSid(ace_sid, user_sid) } == 0 {
            return Err(invalid_security("directory DACL grants access to another SID"));
        }
        Ok(())
    }

    pub(super) fn create_at(path: &Path) -> io::Result<PrivateTempDir> {
        // Allocate the owned name before creating anything on disk.
        let path = path.to_path_buf();
        let wide = wide_path(&path)?;
        let mut user_info = TokenUserBuffer([0; USER_INFO_SIZE]);
        let user_sid = effective_user_sid(&mut user_info)?;
        let mut acl_storage = AclBuffer([0; ACL_SIZE]);
        let acl = acl_storage.0.as_mut_ptr().cast::<ACL>();
        // SAFETY: the stack-backed ACL is aligned and has space for a maximal SID.
        if unsafe { InitializeAcl(acl, ACL_SIZE as u32, ACL_REVISION) } == 0
            || unsafe {
                AddAccessAllowedAceEx(
                    acl, ACL_REVISION, OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
                    FILE_ALL_ACCESS, user_sid,
                )
            } == 0
        {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: SECURITY_DESCRIPTOR can be zero initialized and the documented
        // initialization functions populate it using buffers live through mkdir.
        let mut descriptor: SECURITY_DESCRIPTOR = unsafe { zeroed() };
        let descriptor_ptr = (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast();
        if unsafe { InitializeSecurityDescriptor(descriptor_ptr, SECURITY_DESCRIPTOR_REVISION) } == 0
            || unsafe { SetSecurityDescriptorOwner(descriptor_ptr, user_sid, 0) } == 0
            || unsafe { SetSecurityDescriptorDacl(descriptor_ptr, 1, acl, 0) } == 0
            || unsafe {
                SetSecurityDescriptorControl(descriptor_ptr, SE_DACL_PROTECTED, SE_DACL_PROTECTED)
            } == 0
        {
            return Err(io::Error::last_os_error());
        }
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor_ptr,
            bInheritHandle: 0,
        };
        // The secured descriptor is supplied to the exclusive create operation:
        // there is no interval in which an existing/default DACL protects data.
        if unsafe { CreateDirectoryW(wide.as_ptr(), &attributes) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut directory = PrivateTempDir { path, active: true, handle: None };
        let handle = open_directory(&directory.path)?;
        verify_volume_and_type(&handle)?;
        verify_owner_and_dacl(&handle, user_sid)?;
        directory.handle = Some(handle);
        Ok(directory)
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::fs;
        use windows_sys::Win32::Security::Authorization::{
            ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW,
            SDDL_REVISION_1,
        };
        use windows_sys::Win32::Security::INHERITED_ACE;

        fn create_everyone_inheritable_parent(path: &Path) {
            let sddl: Vec<u16> = "D:P(A;OICI;FA;;;WD)\0".encode_utf16().collect();
            let mut descriptor = null_mut();
            assert_ne!(unsafe {
                ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl.as_ptr(), SDDL_REVISION_1, &mut descriptor, null_mut(),
                )
            }, 0, "{}", io::Error::last_os_error());
            let descriptor = LocalDescriptor(descriptor);
            let attrs = SECURITY_ATTRIBUTES {
                nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
                lpSecurityDescriptor: descriptor.0,
                bInheritHandle: 0,
            };
            let wide = wide_path(path).unwrap();
            assert_ne!(unsafe { CreateDirectoryW(wide.as_ptr(), &attrs) }, 0,
                "{}", io::Error::last_os_error());
        }

        fn assert_only_user_ace(path: &Path, sid: PSID, protected: bool, inherited: bool) {
            let wide = wide_path(path).unwrap();
            let mut owner = null_mut();
            let mut acl: *mut ACL = null_mut();
            let mut descriptor = null_mut();
            let result = unsafe {
                GetNamedSecurityInfoW(
                    wide.as_ptr(), SE_FILE_OBJECT,
                    OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                    &mut owner, null_mut(), &mut acl, null_mut(), &mut descriptor,
                )
            };
            assert_eq!(result, 0, "GetNamedSecurityInfoW: {result}");
            let descriptor = LocalDescriptor(descriptor);
            assert!(!owner.is_null() && !acl.is_null());
            assert_ne!(unsafe { EqualSid(owner, sid) }, 0);
            assert_eq!(unsafe { (*acl).AceCount }, 1);
            let mut control = 0;
            let mut revision = 0;
            assert_ne!(unsafe {
                GetSecurityDescriptorControl(descriptor.0, &mut control, &mut revision)
            }, 0);
            assert_eq!(control & SE_DACL_PROTECTED != 0, protected);
            let mut raw_ace = null_mut();
            assert_ne!(unsafe { GetAce(acl, 0, &mut raw_ace) }, 0);
            let ace = unsafe { &*raw_ace.cast::<ACCESS_ALLOWED_ACE>() };
            assert_eq!(u32::from(ace.Header.AceType), ACCESS_ALLOWED_ACE_TYPE);
            assert_eq!(u32::from(ace.Header.AceFlags) & INHERITED_ACE != 0, inherited);
            assert_eq!(ace.Mask, FILE_ALL_ACCESS);
            assert_ne!(unsafe { EqualSid(addr_of!(ace.SidStart).cast_mut().cast(), sid) }, 0);
        }

        #[test]
        fn protected_user_dacl_blocks_parent_inheritance_and_propagates_to_children() {
            let outer = tempfile::tempdir().unwrap();
            let parent = outer.path().join("everyone-parent");
            create_everyone_inheritable_parent(&parent);
            let mut user_info = TokenUserBuffer([0; USER_INFO_SIZE]);
            let sid = effective_user_sid(&mut user_info).unwrap();
            let parent_handle = open_directory(&parent).unwrap();
            assert!(verify_owner_and_dacl(&parent_handle, sid).is_err());
            drop(parent_handle);

            let private = PrivateTempDir::new_in(&parent).unwrap();
            assert_only_user_ace(private.path(), sid, true, false);
            let child = private.path().join("child.txt");
            fs::write(&child, b"synthetic").unwrap();
            assert_only_user_ace(&child, sid, false, true);
            private.close().unwrap();
        }

        #[test]
        fn volumes_without_persistent_acls_are_rejected() {
            assert_eq!(require_persistent_acls(0).unwrap_err().kind(), io::ErrorKind::Unsupported);
            assert!(require_persistent_acls(FILE_PERSISTENT_ACLS).is_ok());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn close_removes_nested_content_and_drop_also_cleans_up() {
        let parent = tempfile::tempdir().unwrap();
        let directory = PrivateTempDir::new_in(parent.path()).unwrap();
        let path = directory.path().to_path_buf();
        fs::create_dir(path.join("nested")).unwrap();
        fs::write(path.join("nested").join("scratch"), b"synthetic").unwrap();
        directory.close().unwrap();
        assert!(!path.exists());

        let directory = PrivateTempDir::new_in(parent.path()).unwrap();
        let path = directory.path().to_path_buf();
        fs::write(path.join("scratch"), b"synthetic").unwrap();
        drop(directory);
        assert!(!path.exists());
    }

    #[test]
    fn an_existing_directory_is_never_reused_or_modified() {
        let parent = tempfile::tempdir().unwrap();
        let existing = parent.path().join("existing");
        fs::create_dir(&existing).unwrap();
        fs::write(existing.join("keep"), b"unchanged").unwrap();
        assert_eq!(create_at(&existing).unwrap_err().kind(), io::ErrorKind::AlreadyExists);
        assert_eq!(fs::read(existing.join("keep")).unwrap(), b"unchanged");
    }

    #[test]
    fn relative_parent_is_rejected() {
        assert_eq!(
            PrivateTempDir::new_in("relative-parent").unwrap_err().kind(),
            io::ErrorKind::InvalidInput,
        );
    }

    #[cfg(unix)]
    #[test]
    fn unix_creation_grants_no_group_or_other_access() {
        use std::os::unix::fs::PermissionsExt;
        let parent = tempfile::tempdir().unwrap();
        let directory = PrivateTempDir::new_in(parent.path()).unwrap();
        let mode = fs::metadata(directory.path()).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode & 0o077, 0);
        assert_eq!(mode & !0o700, 0);
    }
}
