pub fn recognizes_release() -> bool {
    regex::Regex::new("^release-[0-9]+$").unwrap().is_match("release-37")
}
