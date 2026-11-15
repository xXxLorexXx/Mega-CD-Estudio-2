/*
 * Sega CD ブートセクタ。
 * boot.s / cdcbench_boot.s と同レイアウト。IP=still256(描画Main), SP=cdcbench_sp(待機)。
 */

	.include "disc_region.inc"

DiscHeader:
DiscType:
	.ascii "SEGADISCSYSTEM  "
VolumeName:
	.asciz "CAVC_DMABEN"
VolumeSystem:
	.word 0x0100, 0x0001
SystemName:
	.asciz "SEGASYSTEM "
SystemVersion:
	.word 0x0000, 0x0000
IP_Addr:
	.long 0x00000800
IP_Size:
	.long IPEnd-IPStart
IP_Entry:
	.long 0x00000000
IP_WorkRAM:
	.long 0x00000000
SP_Addr:
	.long 0x00007000
SP_Size:
	.long SPEnd-SPStart
SP_Entry:
	.long 0x00000000
SP_WorkRAM:
	.long 0x00000000
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "

HardwareType:
	disc_hardware_type
Copyright:
	.ascii "(C) AKIYAN 2026 "
DomesticName:
	.ascii "CAVC H40 DMA BENCHMARK                          "
OverseasName:
	.ascii "CAVC H40 DMA BENCHMARK                          "
ProductCode:
	.ascii "GM 00-0000-00   "
IoSupport:
	.ascii "J               "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
	.ascii "                "
Region:
	disc_region_support

	.if . - DiscHeader != 0x200
	.error "dmabench disc header must be exactly 512 bytes"
	.endif

IPStart:
	.incbin "dmabench_ip.bin"
IPEnd:

	.org 0x7000
SPStart:
	.incbin "out/cdcbench_sp.bin"
SPEnd:

	.align 0x8000
