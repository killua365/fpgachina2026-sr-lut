`timescale 1ns/1ps

module sr_lut_rom #(
  parameter int unsigned DEPTH = sr_lut_pkg::X2_LUT_DEPTH,
  parameter int unsigned ADDR_BITS = sr_lut_pkg::X2_ADDR_BITS,
  parameter int unsigned WORD_BITS = sr_lut_pkg::X2_LUT_WORD_BITS,
  parameter string MEM_FILE = ""
) (
  input  logic clk_i,
  input  logic read_enable_a_i,
  input  logic [ADDR_BITS - 1:0] read_address_a_i,
  output logic [WORD_BITS - 1:0] read_data_a_o,
  input  logic read_enable_b_i,
  input  logic [ADDR_BITS - 1:0] read_address_b_i,
  output logic [WORD_BITS - 1:0] read_data_b_o
);

  logic unused_sbiterra;
  logic unused_dbiterra;
  logic unused_sbiterrb;
  logic unused_dbiterrb;

  xpm_memory_tdpram #(
    .MEMORY_SIZE(DEPTH * WORD_BITS),
    .MEMORY_PRIMITIVE("block"),
    .CLOCKING_MODE("common_clock"),
    .MEMORY_INIT_FILE(MEM_FILE),
    .MEMORY_INIT_PARAM(""),
    .USE_MEM_INIT(1),
    .MEMORY_OPTIMIZATION("false"),
    .WRITE_DATA_WIDTH_A(WORD_BITS),
    .READ_DATA_WIDTH_A(WORD_BITS),
    .BYTE_WRITE_WIDTH_A(WORD_BITS),
    .ADDR_WIDTH_A(ADDR_BITS),
    .READ_LATENCY_A(1),
    .WRITE_MODE_A("no_change"),
    .WRITE_DATA_WIDTH_B(WORD_BITS),
    .READ_DATA_WIDTH_B(WORD_BITS),
    .BYTE_WRITE_WIDTH_B(WORD_BITS),
    .ADDR_WIDTH_B(ADDR_BITS),
    .READ_LATENCY_B(1),
    .WRITE_MODE_B("no_change")
  ) lut_memory (
    .sleep(1'b0),
    .clka(clk_i),
    .rsta(1'b0),
    .ena(read_enable_a_i),
    .regcea(1'b1),
    .wea(1'b0),
    .addra(read_address_a_i),
    .dina('0),
    .injectsbiterra(1'b0),
    .injectdbiterra(1'b0),
    .douta(read_data_a_o),
    .sbiterra(unused_sbiterra),
    .dbiterra(unused_dbiterra),
    .clkb(clk_i),
    .rstb(1'b0),
    .enb(read_enable_b_i),
    .regceb(1'b1),
    .web(1'b0),
    .addrb(read_address_b_i),
    .dinb('0),
    .injectsbiterrb(1'b0),
    .injectdbiterrb(1'b0),
    .doutb(read_data_b_o),
    .sbiterrb(unused_sbiterrb),
    .dbiterrb(unused_dbiterrb)
  );

`ifndef SYNTHESIS
  initial begin
    if (MEM_FILE == "") begin
      $fatal(1, "MEM_FILE must identify a LUT initialization file");
    end
  end

  always_ff @(posedge clk_i) begin
    if (read_enable_a_i && (read_address_a_i >= DEPTH)) begin
      $fatal(1, "ROM port A address is out of range");
    end
    if (read_enable_b_i && (read_address_b_i >= DEPTH)) begin
      $fatal(1, "ROM port B address is out of range");
    end
  end
`endif

endmodule
