# Changelog

All notable changes to this project will be documented in this file.

### [1.0.4](https://github.com/yoan-the-human/Make-Another-Me-inator/compare/v1.0.3...v1.0.4) (2026-09-16)


### 🐛 Bugs

* fixed a bug with claude prompt pasting ([2428422](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/2428422291d58f27a72cf49ba5869d6a6c9718b8))

### [1.0.3](https://github.com/yoan-the-human/Make-Another-Me-inator/compare/v1.0.2...v1.0.3) (2026-09-16)


### 🐛 Bugs

* now the claude input is fixed from wrong commands ([372c7e6](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/372c7e67ed147a6091a66e502ee2d0acfbf18c47))

### [1.0.2](https://github.com/yoan-the-human/Make-Another-Me-inator/compare/v1.0.1...v1.0.2) (2026-09-16)


### 🐛 Bugs

* fixed a bug where the script doesnt know what to do on passoword step ([1dedf17](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/1dedf1776ffe837080880197c27b10fee629e052))

### [1.0.1](https://github.com/yoan-the-human/Make-Another-Me-inator/compare/v1.0.0...v1.0.1) (2026-09-16)


### 🐛 Bugs

* fixed a bug where the script didnt write the username and password on the first step ([09e173a](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/09e173a32aad0948b8e20ddcebef9ffe282abf7a))
* merged folder loop bug fixed ([7882467](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/78824672844de0b088b71fc0b056129fc684de1a))

## [1.0.0](https://github.com/yoan-the-human/Make-Another-Me-inator/compare/v0.1.0...v1.0.0) (2026-09-14)


### ⚡ Improvements

* the whole process is majorly simplified. No more worktrees, only branches ([5d063ad](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/5d063ad5f5b58210c3f149e3d969dc76a3d16151))

## 0.1.0 (2026-09-14)


### 🚀 New

* вече се проверява колко токена са останали в claude ([a37e21b](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/a37e21b9e55a2619c4c653491a54f7cfdd5bcd7b))
* now a task can be revisited with the re folder ([a2e505d](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/a2e505dafff3a8d9daf72c811d390c902929a4b9))
* project ([0057f86](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/0057f862d8bf18561ed660eceb2268af4f15a5ad))
* prototype ready ([720ca90](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/720ca902fd798b3b5d2e96b42c55a6ab56cac914))


### ⚡ Improvements

* deletes local branch after completion ([165c16f](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/165c16fbf8160b3c39c515a417d42d46e38c6f70))
* returned the deletion of the worktree after finished task ([6c30b4e](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/6c30b4e0513193c2b0b021d004ecc8d89a771ba1))
* working in the testzone rather than in production ([32ed99a](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/32ed99a4b68f5ba2fe98bb24ac16e07395383d40))


### 🐛 Bugs

* a bug where after git commands - the claude doesnt fire is fixed ([90f65b0](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/90f65b0183492c782457e21322ae7220393119db))
* bug fix when push fails ([edfa8ff](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/edfa8fff86661f4e71b4efe6e1a5b88d4ed4f89e))
* fixed a bug where claude was done, but the script didnt notice ([10c857a](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/10c857af85abf4ccade2e89df821246932d4dc4a))
* fixed all bugs ([d4e2a42](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/d4e2a42ce74e7a8ad310b57993dda555078ddcd6))
* it no longer sends messages that claude asks something, when he is done ([9bd8f6d](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/9bd8f6d1f4b31ea5fb5946dda6b0ac0613567cb7))
* missing import re fix ([b3fe2d8](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/b3fe2d809ac4ca79fb67eec8e2698460cb288ee6))
* now on authentication fail - the script stops and alerts ([8033386](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/80333865e031ce91edacf9411b21032db1235a08))
* now, claude doesnt fire before git has created the worktree ([f090a86](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/f090a86754af0c0fd281249310f586764d1ac806))
* the paste+sent bug is fixed ([81a17ef](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/81a17ef3d9167168c5ddf41a1ca67c749be1280a))
* when creating worktree - it doesnt stay in idle. No more clipboard filling every 1 second. If claude askes a question - script no longer treats it as finished and not longer commits at that situation ([847590b](https://github.com/yoan-the-human/Make-Another-Me-inator/commit/847590bcd6585427f352320ecbd6bd90d36e7c00))
