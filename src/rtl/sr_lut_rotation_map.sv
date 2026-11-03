`timescale 1ns/1ps

module sr_lut_rotation_map #(
  parameter int unsigned SCALE = sr_lut_pkg::X2_SCALE,
  parameter int unsigned ELEMENT_BITS = sr_lut_pkg::NUMERATOR_BITS,
  parameter int unsigned ELEMENTS = SCALE * SCALE
) (
  input  logic [(ELEMENTS * ELEMENT_BITS) - 1:0] block_i,
  input  logic [1:0] direction_i,
  output logic [(ELEMENTS * ELEMENT_BITS) - 1:0] block_o
);

  integer output_row;
  integer output_column;
  integer input_row;
  integer input_column;
  integer input_index;
  integer output_index;

  always_comb begin
    block_o = '0;
    input_row = 0;
    input_column = 0;
    input_index = 0;
    output_index = 0;

    for (output_row = 0; output_row < SCALE;
         output_row = output_row + 1) begin
      for (output_column = 0; output_column < SCALE;
           output_column = output_column + 1) begin
        case (direction_i)
          2'd0: begin
            input_row = output_row;
            input_column = output_column;
          end
          2'd1: begin
            input_row = SCALE - 1 - output_column;
            input_column = output_row;
          end
          2'd2: begin
            input_row = SCALE - 1 - output_row;
            input_column = SCALE - 1 - output_column;
          end
          default: begin
            input_row = output_column;
            input_column = SCALE - 1 - output_row;
          end
        endcase

        input_index = (input_row * SCALE) + input_column;
        output_index = (output_row * SCALE) + output_column;
        block_o[(output_index * ELEMENT_BITS) +: ELEMENT_BITS] =
            block_i[(input_index * ELEMENT_BITS) +: ELEMENT_BITS];
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((SCALE != 2) && (SCALE != 4)) begin
      $fatal(1, "rotation map supports only scale 2 or 4");
    end
    if (ELEMENTS != (SCALE * SCALE)) begin
      $fatal(1, "ELEMENTS must equal SCALE * SCALE");
    end
    if (ELEMENT_BITS == 0) begin
      $fatal(1, "ELEMENT_BITS must be positive");
    end
  end
`endif

endmodule
