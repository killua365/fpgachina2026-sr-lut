`timescale 1ns/1ps

module sr_lut_video420_packer #(
  parameter int unsigned MAX_OUTPUT_WIDTH = 1920
) (
  input  logic        clk_i,
  input  logic        resetn_i,
  input  logic        frame_start_i,
  input  logic [10:0] output_width_i,

  input  logic [23:0] s_rgb_i,
  input  logic        s_valid_i,
  output logic        s_ready_o,
  input  logic        s_last_i,

  output logic [31:0] m_data_o,
  output logic [3:0]  m_keep_o,
  output logic        m_valid_o,
  input  logic        m_ready_i,
  output logic        m_last_o
);
  localparam int unsigned GROUPS_PER_ROW = MAX_OUTPUT_WIDTH / 4;
  localparam int unsigned GROUP_BITS = $clog2(GROUPS_PER_ROW);

  logic [63:0] top_row_mem [0:GROUPS_PER_ROW-1];
  logic [10:0] x_reg;
  logic [10:0] y_reg;
  logic [7:0] y_hold [0:2];
  logic [7:0] u_hold [0:1];
  logic [7:0] v_hold [0:1];
  logic [31:0] packet_word [0:2];
  logic [1:0] packet_index_reg;
  logic packet_valid_reg;
  logic packet_last_reg;

  // Three elastic stages isolate the SR-LUT BRAM, BT.601 multiply/add,
  // and clamp/offset paths while preserving one-pixel-per-cycle throughput.
  logic [23:0] rgb_reg;
  logic rgb_valid_reg;
  logic rgb_last_reg;
  logic signed [19:0] y_sum_reg;
  logic signed [19:0] u_sum_reg;
  logic signed [19:0] v_sum_reg;
  logic sum_valid_reg;
  logic sum_last_reg;
  logic [7:0] pixel_y_reg;
  logic [7:0] pixel_u_reg;
  logic [7:0] pixel_v_reg;
  logic pixel_valid_reg;
  logic pixel_last_reg;

  logic input_fire;
  logic rgb_stage_ready;
  logic sum_stage_ready;
  logic pixel_stage_ready;
  logic packer_ready;
  logic pixel_fire;
  logic output_fire;
  logic final_pixel_of_group;
  logic packet_finishing;
  logic [63:0] top_record;

  function automatic logic [7:0] clamp_u8(input integer value);
    begin
      if (value < 0) clamp_u8 = 8'd0;
      else if (value > 255) clamp_u8 = 8'd255;
      else clamp_u8 = value[7:0];
    end
  endfunction

  function automatic logic signed [19:0] rgb_to_y_sum(input logic [23:0] rgb);
    integer value;
    begin
      value = 66 * rgb[7:0] + 129 * rgb[15:8]
          + 25 * rgb[23:16] + 128;
      rgb_to_y_sum = value;
    end
  endfunction

  function automatic logic signed [19:0] rgb_to_u_sum(input logic [23:0] rgb);
    integer value;
    begin
      value = -38 * rgb[7:0] - 74 * rgb[15:8]
          + 112 * rgb[23:16] + 128;
      rgb_to_u_sum = value;
    end
  endfunction

  function automatic logic signed [19:0] rgb_to_v_sum(input logic [23:0] rgb);
    integer value;
    begin
      value = 112 * rgb[7:0] - 94 * rgb[15:8]
          - 18 * rgb[23:16] + 128;
      rgb_to_v_sum = value;
    end
  endfunction

  function automatic logic [7:0] sum_to_u8(
    input logic signed [19:0] value,
    input integer offset
  );
    integer shifted;
    begin
      shifted = (value >>> 8) + offset;
      sum_to_u8 = clamp_u8(shifted);
    end
  endfunction

  assign final_pixel_of_group = y_reg[0] && (x_reg[1:0] == 2'd3);
  assign output_fire = m_valid_o && m_ready_i;
  assign packet_finishing = output_fire && (packet_index_reg == 2'd2);
  assign packer_ready = !final_pixel_of_group
      || !packet_valid_reg || packet_finishing;
  assign pixel_stage_ready = !pixel_valid_reg || packer_ready;
  assign sum_stage_ready = !sum_valid_reg || pixel_stage_ready;
  assign rgb_stage_ready = !rgb_valid_reg || sum_stage_ready;
  assign s_ready_o = rgb_stage_ready;
  assign input_fire = s_valid_i && s_ready_o;
  assign pixel_fire = pixel_valid_reg && packer_ready;

  assign m_valid_o = packet_valid_reg;
  assign m_data_o = packet_word[packet_index_reg];
  assign m_keep_o = 4'b1111;
  assign m_last_o = packet_valid_reg && packet_last_reg
      && (packet_index_reg == 2'd2);

  always_ff @(posedge clk_i) begin
    if (!resetn_i || frame_start_i) begin
      x_reg <= 0;
      y_reg <= 0;
      y_hold[0] <= 0;
      y_hold[1] <= 0;
      y_hold[2] <= 0;
      u_hold[0] <= 0;
      u_hold[1] <= 0;
      v_hold[0] <= 0;
      v_hold[1] <= 0;
      packet_word[0] <= 0;
      packet_word[1] <= 0;
      packet_word[2] <= 0;
      packet_index_reg <= 0;
      packet_valid_reg <= 1'b0;
      packet_last_reg <= 1'b0;
      rgb_reg <= 0;
      rgb_valid_reg <= 1'b0;
      rgb_last_reg <= 1'b0;
      y_sum_reg <= 0;
      u_sum_reg <= 0;
      v_sum_reg <= 0;
      sum_valid_reg <= 1'b0;
      sum_last_reg <= 1'b0;
      pixel_y_reg <= 0;
      pixel_u_reg <= 0;
      pixel_v_reg <= 0;
      pixel_valid_reg <= 1'b0;
      pixel_last_reg <= 1'b0;
    end else begin
      // Advance the elastic conversion pipeline only when its next stage can
      // accept data. This keeps every register stable during output stalls.
      if (pixel_stage_ready) begin
        pixel_valid_reg <= sum_valid_reg;
        if (sum_valid_reg) begin
          pixel_y_reg <= sum_to_u8(y_sum_reg, 16);
          pixel_u_reg <= sum_to_u8(u_sum_reg, 128);
          pixel_v_reg <= sum_to_u8(v_sum_reg, 128);
          pixel_last_reg <= sum_last_reg;
        end
      end
      if (sum_stage_ready) begin
        sum_valid_reg <= rgb_valid_reg;
        if (rgb_valid_reg) begin
          y_sum_reg <= rgb_to_y_sum(rgb_reg);
          u_sum_reg <= rgb_to_u_sum(rgb_reg);
          v_sum_reg <= rgb_to_v_sum(rgb_reg);
          sum_last_reg <= rgb_last_reg;
        end
      end
      if (rgb_stage_ready) begin
        rgb_valid_reg <= input_fire;
        if (input_fire) begin
          rgb_reg <= s_rgb_i;
          rgb_last_reg <= s_last_i;
        end
      end

      if (output_fire) begin
        if (packet_index_reg == 2'd2) begin
          packet_index_reg <= 0;
          packet_valid_reg <= 1'b0;
          packet_last_reg <= 1'b0;
        end else begin
          packet_index_reg <= packet_index_reg + 1'b1;
        end
      end

      if (pixel_fire) begin
        if (!y_reg[0]) begin
          case (x_reg[1:0])
            2'd0: begin
              y_hold[0] <= pixel_y_reg;
              u_hold[0] <= pixel_u_reg;
              v_hold[0] <= pixel_v_reg;
            end
            2'd1: y_hold[1] <= pixel_y_reg;
            2'd2: begin
              y_hold[2] <= pixel_y_reg;
              u_hold[1] <= pixel_u_reg;
              v_hold[1] <= pixel_v_reg;
            end
            2'd3: begin
              top_row_mem[x_reg[10:2]] <= {
                v_hold[1], u_hold[1], v_hold[0], u_hold[0],
                pixel_y_reg, y_hold[2], y_hold[1], y_hold[0]
              };
            end
          endcase
        end else begin
          case (x_reg[1:0])
            2'd0: y_hold[0] <= pixel_y_reg;
            2'd1: y_hold[1] <= pixel_y_reg;
            2'd2: y_hold[2] <= pixel_y_reg;
            2'd3: begin
              top_record = top_row_mem[x_reg[10:2]];
              packet_word[0] <= {
                y_hold[1], y_hold[0], top_record[15:8], top_record[7:0]
              };
              packet_word[1] <= {
                top_record[31:24], top_record[23:16],
                top_record[47:40], top_record[39:32]
              };
              packet_word[2] <= {
                top_record[63:56], top_record[55:48],
                pixel_y_reg, y_hold[2]
              };
              packet_index_reg <= 0;
              packet_valid_reg <= 1'b1;
              packet_last_reg <= pixel_last_reg;
            end
          endcase
        end

        if (x_reg == output_width_i - 1'b1) begin
          x_reg <= 0;
          y_reg <= y_reg + 1'b1;
        end else begin
          x_reg <= x_reg + 1'b1;
        end
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if ((MAX_OUTPUT_WIDTH < 4) || ((MAX_OUTPUT_WIDTH % 4) != 0))
      $fatal(1, "MAX_OUTPUT_WIDTH must be a positive multiple of four");
  end

  always_ff @(posedge clk_i) begin
    if (resetn_i && frame_start_i && ((output_width_i < 4) || output_width_i[1:0]))
      $fatal(1, "MB420 output width must be a positive multiple of four");
    if (resetn_i && pixel_fire && pixel_last_reg
        && !(y_reg[0] && (x_reg == output_width_i - 1'b1)))
      $fatal(1, "MB420 input TLAST is not on a complete two-row record");
  end
`endif
endmodule
