`timescale 1ns/1ps

// One little-endian 32-bit word per cycle, reflected CRC-32/ISO-HDLC.
// The equations are the balanced GF(2) matrix form of polynomial EDB88320.
module sr_lut_crc32_word (
  input  logic [31:0] crc_i,
  input  logic [31:0] data_i,
  output logic [31:0] crc_o
);
  logic [31:0] value;
  assign value = crc_i ^ data_i;
  assign crc_o[0] = ^{value[0], value[1], value[2], value[3], value[4], value[6], value[7], value[8], value[16], value[20], value[22], value[23], value[26]};
  assign crc_o[1] = ^{value[1], value[2], value[3], value[4], value[5], value[7], value[8], value[9], value[17], value[21], value[23], value[24], value[27]};
  assign crc_o[2] = ^{value[0], value[2], value[3], value[4], value[5], value[6], value[8], value[9], value[10], value[18], value[22], value[24], value[25], value[28]};
  assign crc_o[3] = ^{value[1], value[3], value[4], value[5], value[6], value[7], value[9], value[10], value[11], value[19], value[23], value[25], value[26], value[29]};
  assign crc_o[4] = ^{value[2], value[4], value[5], value[6], value[7], value[8], value[10], value[11], value[12], value[20], value[24], value[26], value[27], value[30]};
  assign crc_o[5] = ^{value[0], value[3], value[5], value[6], value[7], value[8], value[9], value[11], value[12], value[13], value[21], value[25], value[27], value[28], value[31]};
  assign crc_o[6] = ^{value[0], value[2], value[3], value[9], value[10], value[12], value[13], value[14], value[16], value[20], value[23], value[28], value[29]};
  assign crc_o[7] = ^{value[1], value[3], value[4], value[10], value[11], value[13], value[14], value[15], value[17], value[21], value[24], value[29], value[30]};
  assign crc_o[8] = ^{value[0], value[2], value[4], value[5], value[11], value[12], value[14], value[15], value[16], value[18], value[22], value[25], value[30], value[31]};
  assign crc_o[9] = ^{value[0], value[2], value[4], value[5], value[7], value[8], value[12], value[13], value[15], value[17], value[19], value[20], value[22], value[31]};
  assign crc_o[10] = ^{value[0], value[2], value[4], value[5], value[7], value[9], value[13], value[14], value[18], value[21], value[22], value[26]};
  assign crc_o[11] = ^{value[1], value[3], value[5], value[6], value[8], value[10], value[14], value[15], value[19], value[22], value[23], value[27]};
  assign crc_o[12] = ^{value[2], value[4], value[6], value[7], value[9], value[11], value[15], value[16], value[20], value[23], value[24], value[28]};
  assign crc_o[13] = ^{value[0], value[3], value[5], value[7], value[8], value[10], value[12], value[16], value[17], value[21], value[24], value[25], value[29]};
  assign crc_o[14] = ^{value[0], value[1], value[4], value[6], value[8], value[9], value[11], value[13], value[17], value[18], value[22], value[25], value[26], value[30]};
  assign crc_o[15] = ^{value[1], value[2], value[5], value[7], value[9], value[10], value[12], value[14], value[18], value[19], value[23], value[26], value[27], value[31]};
  assign crc_o[16] = ^{value[1], value[4], value[7], value[10], value[11], value[13], value[15], value[16], value[19], value[22], value[23], value[24], value[26], value[27], value[28]};
  assign crc_o[17] = ^{value[2], value[5], value[8], value[11], value[12], value[14], value[16], value[17], value[20], value[23], value[24], value[25], value[27], value[28], value[29]};
  assign crc_o[18] = ^{value[0], value[3], value[6], value[9], value[12], value[13], value[15], value[17], value[18], value[21], value[24], value[25], value[26], value[28], value[29], value[30]};
  assign crc_o[19] = ^{value[0], value[1], value[4], value[7], value[10], value[13], value[14], value[16], value[18], value[19], value[22], value[25], value[26], value[27], value[29], value[30], value[31]};
  assign crc_o[20] = ^{value[0], value[3], value[4], value[5], value[6], value[7], value[11], value[14], value[15], value[16], value[17], value[19], value[22], value[27], value[28], value[30], value[31]};
  assign crc_o[21] = ^{value[0], value[2], value[3], value[5], value[12], value[15], value[17], value[18], value[22], value[26], value[28], value[29], value[31]};
  assign crc_o[22] = ^{value[2], value[7], value[8], value[13], value[18], value[19], value[20], value[22], value[26], value[27], value[29], value[30]};
  assign crc_o[23] = ^{value[0], value[3], value[8], value[9], value[14], value[19], value[20], value[21], value[23], value[27], value[28], value[30], value[31]};
  assign crc_o[24] = ^{value[2], value[3], value[6], value[7], value[8], value[9], value[10], value[15], value[16], value[21], value[23], value[24], value[26], value[28], value[29], value[31]};
  assign crc_o[25] = ^{value[1], value[2], value[6], value[9], value[10], value[11], value[17], value[20], value[23], value[24], value[25], value[26], value[27], value[29], value[30]};
  assign crc_o[26] = ^{value[2], value[3], value[7], value[10], value[11], value[12], value[18], value[21], value[24], value[25], value[26], value[27], value[28], value[30], value[31]};
  assign crc_o[27] = ^{value[0], value[1], value[2], value[6], value[7], value[11], value[12], value[13], value[16], value[19], value[20], value[23], value[25], value[27], value[28], value[29], value[31]};
  assign crc_o[28] = ^{value[0], value[4], value[6], value[12], value[13], value[14], value[16], value[17], value[21], value[22], value[23], value[24], value[28], value[29], value[30]};
  assign crc_o[29] = ^{value[0], value[1], value[5], value[7], value[13], value[14], value[15], value[17], value[18], value[22], value[23], value[24], value[25], value[29], value[30], value[31]};
  assign crc_o[30] = ^{value[3], value[4], value[7], value[14], value[15], value[18], value[19], value[20], value[22], value[24], value[25], value[30], value[31]};
  assign crc_o[31] = ^{value[0], value[1], value[2], value[3], value[5], value[6], value[7], value[15], value[19], value[21], value[22], value[25], value[31]};
endmodule
