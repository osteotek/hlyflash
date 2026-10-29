# BR25 RAM flash loader

`br25loader.bin` is copied unchanged from
[kagaimiq/jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool), commit
`adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a`, path
`data/loaderblobs/usb/br25loader.bin`.

That upstream repository distributes this file alongside its root MIT license;
no separate license notice was present in the loader directory. The upstream
license is reproduced in `LICENSE.jl-uboot-tool.txt`. This records the upstream
licensing and provenance; hlyflash does not claim authorship of the loader.

- Size: 11264 bytes.
- SHA-256: `0bc69023d6bf4c4d5d5bc048e06249ee39c484acd11196ffc73f88940787d77d`.
- Target: BR25 / AC636N, RAM address `0x12000`.
- Format: original MengLi-scrambled upload bytes, unchanged.

The CLI selects this resource only after identifying the chip family as BR25,
checks its SHA-256 before uploading, verifies RAM readback, and then starts it.
This temporary RAM program is separate from the persistent `uboot.boot` firmware
in flash. `--loader` can override the resource, subject to the same hash check.
