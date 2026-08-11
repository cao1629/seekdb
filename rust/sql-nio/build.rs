// Copyright (c) 2025 OceanBase.
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

use std::{env, fs, path::Path, path::PathBuf};

const EXTERN_BLOCK_FILE: &str = "ffi_types.rs";

fn main() {
    let crate_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let out = crate_dir.join("include").join("nio.h");
    std::fs::create_dir_all(out.parent().unwrap()).unwrap();

    check_extern_blocks(&crate_dir.join("src"));

    cbindgen::generate(&crate_dir)
        .expect("cbindgen failed")
        .write_to_file(&out);

    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=cbindgen.toml");
}

fn check_extern_blocks(src: &Path) {
    let mut offenders = Vec::new();
    collect_extern_blocks(src, &mut offenders);
    if offenders.is_empty() {
        return;
    }
    for (file, line) in &offenders {
        println!("cargo:warning={file}:{line}: extern block outside {EXTERN_BLOCK_FILE}");
    }
    panic!(
        "{} extern block(s) outside src/{EXTERN_BLOCK_FILE}: every foreign function \
         declaration must live in that file",
        offenders.len()
    );
}

fn collect_extern_blocks(dir: &Path, offenders: &mut Vec<(String, usize)>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            collect_extern_blocks(&path, offenders);
        } else if path.extension().is_some_and(|ext| ext == "rs")
            && path.file_name().is_some_and(|name| name != EXTERN_BLOCK_FILE)
        {
            let source = fs::read_to_string(&path).unwrap();
            for line in extern_block_lines(&source) {
                offenders.push((path.display().to_string(), line));
            }
        }
    }
}

fn extern_block_lines(source: &str) -> Vec<usize> {
    let mut code = Vec::new();
    for (index, line) in source.lines().enumerate() {
        for ch in line.split("//").next().unwrap_or("").chars() {
            code.push((ch, index + 1));
        }
        code.push((' ', index + 1));
    }

    let mut found = Vec::new();
    let text: String = code.iter().map(|(ch, _)| *ch).collect();
    let mut at = 0;
    while let Some(hit) = text[at..].find("extern") {
        let start = at + hit;
        at = start + "extern".len();
        let before_ok = start == 0 || !is_word_char(text[..start].chars().next_back().unwrap());
        if before_ok && next_token_opens_block(&text[at..]) {
            found.push(code[start].1);
        }
    }
    found
}

fn next_token_opens_block(rest: &str) -> bool {
    let rest = rest.trim_start();
    let rest = match rest.strip_prefix('"') {
        Some(after_quote) => match after_quote.find('"') {
            Some(end) => after_quote[end + 1..].trim_start(),
            None => return false,
        },
        None => rest,
    };
    rest.starts_with('{')
}

fn is_word_char(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}
