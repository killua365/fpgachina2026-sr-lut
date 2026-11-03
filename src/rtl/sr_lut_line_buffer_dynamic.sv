`timescale 1ns/1ps

module sr_lut_line_buffer_dynamic #(
  parameter int unsigned MAX_FRAME_WIDTH = 480,
  parameter int unsigned MAX_FRAME_HEIGHT = 320,
  parameter int unsigned RGB_BITS = 24,
  parameter int unsigned CHANNEL_BITS = 8,
  parameter int unsigned CHANNELS = 3,
  parameter int unsigned NEIGHBOR_BITS = 32
) (
  input  logic clk_i,
  input  logic resetn_i,
  input  logic [RGB_BITS - 1:0] s_axis_tdata_i,
  input  logic s_axis_tvalid_i,
  output logic s_axis_tready_o,
  input  logic s_axis_tlast_i,
  input  logic [9:0] active_width_i,
  input  logic [9:0] active_height_i,
  input  logic frame_start_i,
  output logic [NEIGHBOR_BITS - 1:0] neighborhood_data_o,
  output logic neighborhood_valid_o,
  input  logic neighborhood_ready_i,
  output logic neighborhood_last_o
);

  localparam int unsigned EXPECTED_NEIGHBOR_BITS = CHANNEL_BITS * 4;
  localparam int unsigned ROW_BITS =
      (MAX_FRAME_HEIGHT <= 2) ? 1 : $clog2(MAX_FRAME_HEIGHT);
  localparam int unsigned COLUMN_BITS =
      (MAX_FRAME_WIDTH <= 2) ? 1 : $clog2(MAX_FRAME_WIDTH);

  typedef enum logic {
    STATE_INPUT,
    STATE_OUTPUT
  } state_t;

  state_t state;
  logic frame_active;
  (* keep = "yes" *) logic [COLUMN_BITS - 1:0] frame_last_column;
  (* keep = "yes" *) logic [COLUMN_BITS - 1:0] frame_penultimate_column;
  (* keep = "yes" *) logic [ROW_BITS - 1:0] frame_last_row;
  (* keep = "yes" *) logic [ROW_BITS - 1:0] frame_penultimate_row;

  logic [ROW_BITS - 1:0] input_row;
  logic [COLUMN_BITS - 1:0] input_column;
  logic [1:0] input_bank;
  logic [ROW_BITS - 1:0] output_center_row;
  logic [COLUMN_BITS - 1:0] output_column;
  logic [1:0] output_center_bank;
  logic [1:0] output_direction;
  logic [1:0] output_channel;
  logic pending_last_row;

  logic [COLUMN_BITS - 1:0] left_column;
  logic [COLUMN_BITS - 1:0] right_column;
  logic [1:0] neighbor_row_bank;

  logic [1:0] neighbor_bank_0;
  logic [1:0] neighbor_bank_1;
  logic [1:0] neighbor_bank_2;
  logic [1:0] neighbor_bank_3;
  logic [COLUMN_BITS - 1:0] common_column_0;
  logic [COLUMN_BITS - 1:0] common_column_1;

  logic bank_0_write_enable;
  logic bank_1_write_enable;
  logic bank_2_write_enable;
  logic [RGB_BITS - 1:0] bank_0_read_0;
  logic [RGB_BITS - 1:0] bank_0_read_1;
  logic [RGB_BITS - 1:0] bank_1_read_0;
  logic [RGB_BITS - 1:0] bank_1_read_1;
  logic [RGB_BITS - 1:0] bank_2_read_0;
  logic [RGB_BITS - 1:0] bank_2_read_1;

  logic [RGB_BITS - 1:0] neighbor_pixel_0;
  logic [RGB_BITS - 1:0] neighbor_pixel_1;
  logic [RGB_BITS - 1:0] neighbor_pixel_2;
  logic [RGB_BITS - 1:0] neighbor_pixel_3;
  logic [31:0] neighborhood_data_fixed;

  assign neighborhood_data_o = neighborhood_data_fixed;

  function automatic logic [1:0] next_bank(input logic [1:0] bank);
    begin
      if (bank == 2) begin
        next_bank = 0;
      end else begin
        next_bank = bank + 1'b1;
      end
    end
  endfunction

  function automatic logic [1:0] previous_bank(input logic [1:0] bank);
    begin
      if (bank == 0) begin
        previous_bank = 2;
      end else begin
        previous_bank = bank - 1'b1;
      end
    end
  endfunction

  function automatic logic [CHANNEL_BITS - 1:0] read_channel(
    input logic [RGB_BITS - 1:0] pixel,
    input logic [1:0] channel_index
  );
    begin
      case (channel_index)
        0: read_channel = pixel[(0 * CHANNEL_BITS) +: CHANNEL_BITS];
        1: read_channel = pixel[(1 * CHANNEL_BITS) +: CHANNEL_BITS];
        default: read_channel = pixel[(2 * CHANNEL_BITS) +: CHANNEL_BITS];
      endcase
    end
  endfunction

  assign s_axis_tready_o = frame_active && (state == STATE_INPUT);
  assign neighborhood_valid_o = frame_active && (state == STATE_OUTPUT);
  assign neighborhood_last_o =
      (state == STATE_OUTPUT)
      && (output_center_row == frame_last_row)
      && (output_column == frame_last_column)
      && (output_direction == 3)
      && (output_channel == 2);

  assign bank_0_write_enable =
      resetn_i && (state == STATE_INPUT) && s_axis_tvalid_i
      && s_axis_tready_o && (input_bank == 0);
  assign bank_1_write_enable =
      resetn_i && (state == STATE_INPUT) && s_axis_tvalid_i
      && s_axis_tready_o && (input_bank == 1);
  assign bank_2_write_enable =
      resetn_i && (state == STATE_INPUT) && s_axis_tvalid_i
      && s_axis_tready_o && (input_bank == 2);

  sr_lut_line_memory_2r_dynamic #(
    .DEPTH(MAX_FRAME_WIDTH), .DATA_BITS(RGB_BITS), .ADDR_BITS(COLUMN_BITS)
  ) line_memory_0 (
    .clk_i(clk_i), .write_enable_i(bank_0_write_enable),
    .write_address_i(input_column), .write_data_i(s_axis_tdata_i),
    .read_address_0_i(common_column_0),
    .read_address_1_i(common_column_1),
    .read_data_0_o(bank_0_read_0), .read_data_1_o(bank_0_read_1)
  );

  sr_lut_line_memory_2r_dynamic #(
    .DEPTH(MAX_FRAME_WIDTH), .DATA_BITS(RGB_BITS), .ADDR_BITS(COLUMN_BITS)
  ) line_memory_1 (
    .clk_i(clk_i), .write_enable_i(bank_1_write_enable),
    .write_address_i(input_column), .write_data_i(s_axis_tdata_i),
    .read_address_0_i(common_column_0),
    .read_address_1_i(common_column_1),
    .read_data_0_o(bank_1_read_0), .read_data_1_o(bank_1_read_1)
  );

  sr_lut_line_memory_2r_dynamic #(
    .DEPTH(MAX_FRAME_WIDTH), .DATA_BITS(RGB_BITS), .ADDR_BITS(COLUMN_BITS)
  ) line_memory_2 (
    .clk_i(clk_i), .write_enable_i(bank_2_write_enable),
    .write_address_i(input_column), .write_data_i(s_axis_tdata_i),
    .read_address_0_i(common_column_0),
    .read_address_1_i(common_column_1),
    .read_data_0_o(bank_2_read_0), .read_data_1_o(bank_2_read_1)
  );

  always_comb begin
    neighbor_bank_0 = output_center_bank;
    neighbor_bank_1 = output_center_bank;
    neighbor_bank_2 = output_center_bank;
    neighbor_bank_3 = output_center_bank;
    common_column_0 = output_column;
    common_column_1 = output_column;
    left_column = output_column;
    right_column = output_column;
    neighbor_row_bank = output_center_bank;

    if (state == STATE_OUTPUT) begin
      if (output_column == 0) begin
        left_column = 1;
      end else begin
        left_column = output_column - 1'b1;
      end

      if (output_column == frame_last_column) begin
        right_column = frame_penultimate_column;
      end else begin
        right_column = output_column + 1'b1;
      end

      // Each direction uses only the center column and one lateral column.
      // Sharing these two addresses across all three row banks halves the
      // distributed-memory replication without changing output throughput.
      if (output_direction[1] == output_direction[0]) begin
        common_column_1 = right_column;
      end else begin
        common_column_1 = left_column;
      end

      if (output_direction[1]) begin
        if (output_center_row == 0) begin
          neighbor_row_bank = next_bank(output_center_bank);
        end else begin
          neighbor_row_bank = previous_bank(output_center_bank);
        end
      end else begin
        if (output_center_row == frame_last_row) begin
          neighbor_row_bank = previous_bank(output_center_bank);
        end else begin
          neighbor_row_bank = next_bank(output_center_bank);
        end
      end

      case (output_direction)
        0: begin
          neighbor_bank_0 = output_center_bank;
          neighbor_bank_1 = output_center_bank;
          neighbor_bank_2 = neighbor_row_bank;
          neighbor_bank_3 = neighbor_row_bank;
        end

        1: begin
          neighbor_bank_0 = output_center_bank;
          neighbor_bank_1 = neighbor_row_bank;
          neighbor_bank_2 = output_center_bank;
          neighbor_bank_3 = neighbor_row_bank;
        end

        2: begin
          neighbor_bank_0 = output_center_bank;
          neighbor_bank_1 = output_center_bank;
          neighbor_bank_2 = neighbor_row_bank;
          neighbor_bank_3 = neighbor_row_bank;
        end

        default: begin
          neighbor_bank_0 = output_center_bank;
          neighbor_bank_1 = neighbor_row_bank;
          neighbor_bank_2 = output_center_bank;
          neighbor_bank_3 = neighbor_row_bank;
        end
      endcase
    end
  end

  always_comb begin
    neighbor_pixel_0 = '0;
    neighbor_pixel_1 = '0;
    neighbor_pixel_2 = '0;
    neighbor_pixel_3 = '0;
    case (neighbor_bank_0)
      0: neighbor_pixel_0 = bank_0_read_0;
      1: neighbor_pixel_0 = bank_1_read_0;
      default: neighbor_pixel_0 = bank_2_read_0;
    endcase
    case (neighbor_bank_1)
      0: neighbor_pixel_1 = output_direction[0]
          ? bank_0_read_0 : bank_0_read_1;
      1: neighbor_pixel_1 = output_direction[0]
          ? bank_1_read_0 : bank_1_read_1;
      default: neighbor_pixel_1 = output_direction[0]
          ? bank_2_read_0 : bank_2_read_1;
    endcase
    case (neighbor_bank_2)
      0: neighbor_pixel_2 = output_direction[0]
          ? bank_0_read_1 : bank_0_read_0;
      1: neighbor_pixel_2 = output_direction[0]
          ? bank_1_read_1 : bank_1_read_0;
      default: neighbor_pixel_2 = output_direction[0]
          ? bank_2_read_1 : bank_2_read_0;
    endcase
    case (neighbor_bank_3)
      0: neighbor_pixel_3 = bank_0_read_1;
      1: neighbor_pixel_3 = bank_1_read_1;
      default: neighbor_pixel_3 = bank_2_read_1;
    endcase
  end

  always_comb begin
    neighborhood_data_fixed = '0;
    if (state == STATE_OUTPUT) begin
      // Keep the lane order explicit: lane 0 occupies the least-significant
      // byte consumed first by the downstream scheduler.
      neighborhood_data_fixed = {
        read_channel(neighbor_pixel_3, output_channel),
        read_channel(neighbor_pixel_2, output_channel),
        read_channel(neighbor_pixel_1, output_channel),
        read_channel(neighbor_pixel_0, output_channel)
      };
    end
  end

  always_ff @(posedge clk_i) begin
    if (!resetn_i) begin
      state <= STATE_INPUT;
      frame_active <= 1'b0;
      frame_last_column <= MAX_FRAME_WIDTH - 1;
      frame_penultimate_column <= MAX_FRAME_WIDTH - 2;
      frame_last_row <= MAX_FRAME_HEIGHT - 1;
      frame_penultimate_row <= MAX_FRAME_HEIGHT - 2;
      input_row <= 0;
      input_column <= 0;
      input_bank <= 0;
      output_center_row <= 0;
      output_column <= 0;
      output_center_bank <= 0;
      output_direction <= 0;
      output_channel <= 0;
      pending_last_row <= 1'b0;
    end else if (frame_start_i) begin
      state <= STATE_INPUT;
      frame_active <= 1'b1;
      frame_last_column <= active_width_i - 1'b1;
      frame_penultimate_column <= active_width_i - 2'd2;
      frame_last_row <= active_height_i - 1'b1;
      frame_penultimate_row <= active_height_i - 2'd2;
      input_row <= '0;
      input_column <= '0;
      input_bank <= '0;
      output_center_row <= '0;
      output_column <= '0;
      output_center_bank <= '0;
      output_direction <= '0;
      output_channel <= '0;
      pending_last_row <= 1'b0;
    end else begin
      case (state)
        STATE_INPUT: begin
          if (s_axis_tvalid_i && s_axis_tready_o) begin
            if (input_column == frame_last_column) begin
              input_column <= 0;

              if (input_row == 0) begin
                input_row <= 1;
                input_bank <= next_bank(input_bank);
              end else if (input_row == frame_last_row) begin
                output_center_row <= frame_penultimate_row;
                output_center_bank <= previous_bank(input_bank);
                output_column <= 0;
                output_direction <= 0;
                output_channel <= 0;
                pending_last_row <= 1'b1;
                state <= STATE_OUTPUT;
              end else begin
                output_center_row <= input_row - 1;
                output_center_bank <= previous_bank(input_bank);
                output_column <= 0;
                output_direction <= 0;
                output_channel <= 0;
                pending_last_row <= 1'b0;
                input_row <= input_row + 1;
                input_bank <= next_bank(input_bank);
                state <= STATE_OUTPUT;
              end
            end else begin
              input_column <= input_column + 1;
            end
          end
        end

        STATE_OUTPUT: begin
          if (neighborhood_valid_o && neighborhood_ready_i) begin
            if (output_channel == 2) begin
              output_channel <= 0;
              if (output_direction == 3) begin
                output_direction <= 0;
                if (output_column == frame_last_column) begin
                  output_column <= 0;
                  if (pending_last_row) begin
                    output_center_row <= frame_last_row;
                    output_center_bank <= input_bank;
                    pending_last_row <= 1'b0;
                  end else begin
                    state <= STATE_INPUT;
                    if (output_center_row == frame_last_row) begin
                      input_row <= 0;
                      input_column <= 0;
                      input_bank <= 0;
                      frame_active <= 1'b0;
                    end
                  end
                end else begin
                  output_column <= output_column + 1;
                end
              end else begin
                output_direction <= output_direction + 1;
              end
            end else begin
              output_channel <= output_channel + 1;
            end
          end
        end

        default: begin
          state <= STATE_INPUT;
        end
      endcase
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (MAX_FRAME_WIDTH < 2 || MAX_FRAME_HEIGHT < 2) begin
      $fatal(1, "line buffer requires width and height >= 2");
    end
    if (RGB_BITS != (CHANNEL_BITS * CHANNELS)) begin
      $fatal(1, "RGB_BITS must equal CHANNEL_BITS * CHANNELS");
    end
    if (CHANNELS != 3) begin
      $fatal(1, "line buffer output order requires exactly three RGB channels");
    end
    if (NEIGHBOR_BITS != EXPECTED_NEIGHBOR_BITS) begin
      $fatal(1, "NEIGHBOR_BITS must equal four channel values");
    end
  end

`endif

endmodule
// Two-read distributed line memory. The synthesis branch uses explicit
// RAM64M primitives because Vivado's inferred RAM64M maps its fourth data
// slice to the write-address port, which is not an independent read port.
module sr_lut_line_memory_2r_dynamic #(
  parameter int unsigned DEPTH = 4,
  parameter int unsigned DATA_BITS = 24,
  parameter int unsigned ADDR_BITS = 2
) (
  input logic clk_i,
  input logic write_enable_i,
  input logic [ADDR_BITS - 1:0] write_address_i,
  input logic [DATA_BITS - 1:0] write_data_i,
  input logic [ADDR_BITS - 1:0] read_address_0_i,
  input logic [ADDR_BITS - 1:0] read_address_1_i,
  output logic [DATA_BITS - 1:0] read_data_0_o,
  output logic [DATA_BITS - 1:0] read_data_1_o
);

  localparam int unsigned SEGMENT_COUNT = (DEPTH + 63) / 64;

`ifndef SYNTHESIS
  logic [DATA_BITS - 1:0] line_memory [0:DEPTH - 1];

  always_ff @(posedge clk_i) begin
    if (write_enable_i) begin
      line_memory[write_address_i] <= write_data_i;
    end
  end

  always_comb begin
    read_data_0_o = line_memory[read_address_0_i];
    read_data_1_o = line_memory[read_address_1_i];
  end
`else
  logic [5:0] write_address_low;
  logic [5:0] read_address_0_low;
  logic [5:0] read_address_1_low;
  logic [DATA_BITS - 1:0] read_data_0_segments [0:SEGMENT_COUNT - 1];
  logic [DATA_BITS - 1:0] read_data_1_segments [0:SEGMENT_COUNT - 1];

  assign write_address_low = write_address_i;
  assign read_address_0_low = read_address_0_i;
  assign read_address_1_low = read_address_1_i;

  assign read_data_0_o = read_data_0_segments[read_address_0_i >> 6];
  assign read_data_1_o = read_data_1_segments[read_address_1_i >> 6];

  genvar segment_index;
  genvar bit_group;
  generate
    for (segment_index = 0; segment_index < SEGMENT_COUNT; segment_index++) begin : gen_segment
      wire segment_write_enable =
          write_enable_i && ((write_address_i >> 6) == segment_index);
      for (bit_group = 0; bit_group < (DATA_BITS / 3); bit_group++) begin : gen_group
        localparam int unsigned BIT_BASE = bit_group * 3;

        (* dont_touch = "yes" *)
        RAM64M copy_0 (
          .DOA(read_data_0_segments[segment_index][BIT_BASE]),
          .DOB(read_data_0_segments[segment_index][BIT_BASE + 1]),
          .DOC(read_data_0_segments[segment_index][BIT_BASE + 2]),
          .DOD(),
          .ADDRA(read_address_0_low), .ADDRB(read_address_0_low),
          .ADDRC(read_address_0_low), .ADDRD(write_address_low),
          .DIA(write_data_i[BIT_BASE]),
          .DIB(write_data_i[BIT_BASE + 1]),
          .DIC(write_data_i[BIT_BASE + 2]), .DID(1'b0),
          .WCLK(clk_i), .WE(segment_write_enable)
        );

        (* dont_touch = "yes" *)
        RAM64M copy_1 (
          .DOA(read_data_1_segments[segment_index][BIT_BASE]),
          .DOB(read_data_1_segments[segment_index][BIT_BASE + 1]),
          .DOC(read_data_1_segments[segment_index][BIT_BASE + 2]),
          .DOD(),
          .ADDRA(read_address_1_low), .ADDRB(read_address_1_low),
          .ADDRC(read_address_1_low), .ADDRD(write_address_low),
          .DIA(write_data_i[BIT_BASE]),
          .DIB(write_data_i[BIT_BASE + 1]),
          .DIC(write_data_i[BIT_BASE + 2]), .DID(1'b0),
          .WCLK(clk_i), .WE(segment_write_enable)
        );
      end
    end
  endgenerate
`endif

`ifndef SYNTHESIS
  initial begin
    if (DEPTH < 2 || DATA_BITS == 0 || (DATA_BITS % 3) != 0) begin
      $fatal(1, "invalid distributed line memory parameters");
    end
  end
`endif

endmodule
