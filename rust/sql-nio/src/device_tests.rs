// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! Shared Rust runtime cases and the feature-gated iOS device-test ABI.

use crate::cert::{format_display_name, PeerCertificateInfo};
use crate::tls::tls_cipher_name;

/// Verify truncated DER is rejected without producing trusted peer metadata.
pub(crate) fn rejects_truncated_certificate() -> Result<(), String> {
    if PeerCertificateInfo::parse(&[0x30, 0x00]).valid {
        Err("truncated certificate was accepted".to_owned())
    } else {
        Ok(())
    }
}

/// Verify X.509 display names use the slash-delimited SQL account form.
pub(crate) fn formats_display_name_for_sql_account() -> Result<(), String> {
    if format_display_name("CN=allowed2") != b"/CN=allowed2" {
        return Err("single common name was formatted incorrectly".to_owned());
    }
    if format_display_name("CN=allowed2, O=seekdb") != b"/CN=allowed2/O=seekdb" {
        return Err("multi-component name was formatted incorrectly".to_owned());
    }
    Ok(())
}

/// Verify representative rustls suites retain seekdb's SQL cipher names.
pub(crate) fn exposes_sql_cipher_names() -> Result<(), String> {
    if tls_cipher_name(rustls::CipherSuite::TLS13_AES_256_GCM_SHA384)
        != Some(&b"TLS_AES_256_GCM_SHA384"[..])
    {
        return Err("TLS 1.3 cipher name differs".to_owned());
    }
    if tls_cipher_name(rustls::CipherSuite::TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256)
        != Some(&b"ECDHE-RSA-AES128-GCM-SHA256"[..])
    {
        return Err("TLS 1.2 cipher name differs".to_owned());
    }
    Ok(())
}

#[cfg(test)]
mod host_tests {
    use super::*;

    #[test]
    fn test_rejects_truncated_certificate() {
        rejects_truncated_certificate().unwrap()
    }

    #[test]
    fn test_formats_display_name_for_sql_account() {
        formats_display_name_for_sql_account().unwrap()
    }

    #[test]
    fn test_exposes_sql_cipher_names() {
        exposes_sql_cipher_names().unwrap()
    }
}

#[cfg(feature = "ios-device-tests")]
mod ffi {
    use super::*;
    use std::ffi::c_char;
    use std::panic::{catch_unwind, AssertUnwindSafe};
    use std::ptr;
    use std::sync::atomic::{AtomicBool, Ordering};

    pub const NIO_DEVICE_TEST_OK: u32 = 0;
    pub const NIO_DEVICE_TEST_FAILED: u32 = 1;
    pub const NIO_DEVICE_TEST_INVALID_INDEX: u32 = 2;
    pub const NIO_DEVICE_TEST_INVALID_CAPACITY: u32 = 3;
    pub const NIO_DEVICE_TEST_PANIC: u32 = 4;
    pub const NIO_DEVICE_TEST_ID_CAPACITY: usize = 64;
    pub const NIO_DEVICE_TEST_DIAGNOSTIC_CAPACITY: usize = 256;
    static PANIC_CONTAINED: AtomicBool = AtomicBool::new(false);

    type CaseFunction = fn() -> Result<(), String>;

    struct Case {
        id: &'static str,
        function: CaseFunction,
        expected_panic: bool,
    }

    fn intentional_panic() -> Result<(), String> {
        panic!("intentional device-test panic");
    }

    fn panic_continuation() -> Result<(), String> {
        if PANIC_CONTAINED.load(Ordering::SeqCst) {
            Ok(())
        } else {
            Err("panic_contained marker was not set".to_owned())
        }
    }

    const CASES: &[Case] = &[
        Case {
            id: "ios.rust.cert.rejects_truncated_certificate",
            function: rejects_truncated_certificate,
            expected_panic: false,
        },
        Case {
            id: "ios.rust.cert.formats_display_name_for_sql_account",
            function: formats_display_name_for_sql_account,
            expected_panic: false,
        },
        Case {
            id: "ios.rust.tls.exposes_sql_cipher_names",
            function: exposes_sql_cipher_names,
            expected_panic: false,
        },
        Case {
            id: "ios.rust.device.intentional_panic",
            function: intentional_panic,
            expected_panic: true,
        },
        Case {
            id: "ios.rust.device.panic_continuation",
            function: panic_continuation,
            expected_panic: false,
        },
    ];

    #[repr(C)]
    pub struct NioDeviceTestCaseInfo {
        pub id: [c_char; NIO_DEVICE_TEST_ID_CAPACITY],
    }

    #[repr(C)]
    pub struct NioDeviceTestResult {
        pub status: u32,
        pub diagnostic_len: u32,
        pub diagnostic: [u8; NIO_DEVICE_TEST_DIAGNOSTIC_CAPACITY],
    }

    fn write_diagnostic(result: &mut NioDeviceTestResult, message: &str) {
        let bytes = message.as_bytes();
        let length = bytes.len().min(result.diagnostic.len().saturating_sub(1));
        result.diagnostic[..length].copy_from_slice(&bytes[..length]);
        result.diagnostic[length] = 0;
        result.diagnostic_len = length as u32;
    }

    #[no_mangle]
    pub extern "C" fn nio_device_test_count() -> u32 {
        CASES.len() as u32
    }

    #[no_mangle]
    pub unsafe extern "C" fn nio_device_test_case_info(
        index: u32,
        output: *mut NioDeviceTestCaseInfo,
        capacity: usize,
    ) -> u32 {
        let Some(case) = CASES.get(index as usize) else {
            return NIO_DEVICE_TEST_INVALID_INDEX;
        };
        if output.is_null() || capacity < std::mem::size_of::<NioDeviceTestCaseInfo>() {
            return NIO_DEVICE_TEST_INVALID_CAPACITY;
        }
        let output = &mut *output;
        *output = NioDeviceTestCaseInfo {
            id: [0; NIO_DEVICE_TEST_ID_CAPACITY],
        };
        if case.id.len() >= output.id.len() {
            return NIO_DEVICE_TEST_INVALID_CAPACITY;
        }
        ptr::copy_nonoverlapping(
            case.id.as_ptr().cast::<c_char>(),
            output.id.as_mut_ptr(),
            case.id.len(),
        );
        NIO_DEVICE_TEST_OK
    }

    #[no_mangle]
    pub unsafe extern "C" fn nio_device_test_run(
        index: u32,
        output: *mut NioDeviceTestResult,
        capacity: usize,
    ) -> u32 {
        let Some(case) = CASES.get(index as usize) else {
            return NIO_DEVICE_TEST_INVALID_INDEX;
        };
        if output.is_null() || capacity < std::mem::size_of::<NioDeviceTestResult>() {
            return NIO_DEVICE_TEST_INVALID_CAPACITY;
        }
        let output = &mut *output;
        *output = NioDeviceTestResult {
            status: NIO_DEVICE_TEST_OK,
            diagnostic_len: 0,
            diagnostic: [0; NIO_DEVICE_TEST_DIAGNOSTIC_CAPACITY],
        };
        match catch_unwind(AssertUnwindSafe(|| (case.function)())) {
            Ok(Ok(())) => output.status = NIO_DEVICE_TEST_OK,
            Ok(Err(message)) => {
                output.status = NIO_DEVICE_TEST_FAILED;
                write_diagnostic(output, &message);
            }
            Err(_) => {
                output.status = NIO_DEVICE_TEST_PANIC;
                write_diagnostic(output, "intentional panic captured at Rust FFI boundary");
                if case.expected_panic {
                    PANIC_CONTAINED.store(true, Ordering::SeqCst);
                }
            }
        }
        output.status
    }
}

#[cfg(feature = "ios-device-tests")]
#[allow(unused_imports)]
pub use ffi::*;
