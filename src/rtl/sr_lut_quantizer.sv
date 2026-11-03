`timescale 1ns/1ps

module sr_lut_quantizer #(
  parameter int unsigned PIXEL_BITS = sr_lut_pkg::PIXEL_BITS,
  parameter int unsigned SAMPLE_BITS = sr_lut_pkg::X2_SAMPLE_BITS,
  parameter int unsigned LEVELS = (1 << (PIXEL_BITS - SAMPLE_BITS)) + 1,
  parameter int unsigned LOWER_BITS = $clog2(LEVELS),
  parameter int unsigned LANES = sr_lut_pkg::NEIGHBOR_COUNT
) (
  input  logic [(LANES * PIXEL_BITS) - 1:0] pixels_i,
  output logic [(LANES * LOWER_BITS) - 1:0] lower_o,
  output logic [(LANES * SAMPLE_BITS) - 1:0] remainder_o
);

  generate
    for (genvar lane = 0; lane < LANES; lane++) begin : gen_quantize_lane
      logic [PIXEL_BITS - 1:0] pixel;

      assign pixel = pixels_i[(lane * PIXEL_BITS) +: PIXEL_BITS];
      assign lower_o[(lane * LOWER_BITS) +: LOWER_BITS] =
          pixel >> SAMPLE_BITS;
      assign remainder_o[(lane * SAMPLE_BITS) +: SAMPLE_BITS] =
          pixel[SAMPLE_BITS - 1:0];
    end
  endgenerate

`ifndef SYNTHESIS
  initial begin
    if ((SAMPLE_BITS == 0) || (SAMPLE_BITS >= PIXEL_BITS)) begin
      $fatal(1, "SAMPLE_BITS must be between 1 and PIXEL_BITS-1");
    end
    if (LEVELS != ((1 << (PIXEL_BITS - SAMPLE_BITS)) + 1)) begin
      $fatal(1, "LEVELS does not match PIXEL_BITS and SAMPLE_BITS");
    end
    if (LOWER_BITS != $clog2(LEVELS)) begin
      $fatal(1, "LOWER_BITS must equal $clog2(LEVELS)");
    end
  end
`endif

endmodule
