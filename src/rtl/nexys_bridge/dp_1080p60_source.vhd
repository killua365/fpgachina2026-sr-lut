-- Original SR-LUT N2 pattern source. Two HBR lanes, RGB888, synchronous clock.
-- 135 MHz fabric emits two symbols per lane per cycle. TU=40 symbols with
-- 33 payload symbols: 148.5*3/(270*2)=33/40. Optional TU64 uses 52/53
-- payload symbols with exact 4/5 accumulation. This is a compatibility probe.
-- 2000 fabric clocks/line, 1125 lines/frame => exactly 60 Hz at nominal clock.
-- This is a pre-scrambler source, not proof of a physical DisplayPort link.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dp_1080p60_source is
    generic (initial_frame : natural range 0 to 65535 := 0;
             timestamp_scale : positive range 1 to 2048 := 1;
             transfer_unit_size : positive range 32 to 64 := 40);
    port (clk : in std_logic;
          reset : in std_logic := '0';
          stream_channel_count : out std_logic_vector(2 downto 0);
          ready : out std_logic;
          data : out std_logic_vector(72 downto 0) := (others => '0'));
end entity;

architecture rtl of dp_1080p60_source is
    subtype symbol is std_logic_vector(8 downto 0);
    subtype rgb is std_logic_vector(23 downto 0);
    type symbol_array is array(natural range <>) of symbol;
    -- Equivalent synchronous timestamp representations; no clock/rate change.
    -- Scale 2048 is a receiver-compatibility experiment, not a protocol fix.
    function timestamp_byte(value, position : natural) return symbol is
    begin
        return '0' & std_logic_vector(to_unsigned((value/(256**position)) mod 256,8));
    end function;
    constant mvid : natural := 11*timestamp_scale;
    constant nvid : natural := 20*timestamp_scale;
    constant BS : symbol := '1' & x"BC";
    constant BE : symbol := '1' & x"FB";
    constant FS : symbol := '1' & x"FE";
    constant FE : symbol := '1' & x"F7";
    constant SS : symbol := '1' & x"5C";
    constant SE : symbol := '1' & x"FD";
    constant payload_floor : natural := transfer_unit_size*33/40;
    constant payload_remainder : natural := (transfer_unit_size*33) mod 40;
    -- Elaborated constant: the last TU ends immediately after the last pixel,
    -- without FS/FE. Its shortened length is part of the transport contract.
    function active_length return positive is
        variable remaining : natural := 2880;
        variable used : natural := 0;
        variable valid : natural := payload_floor;
        variable fraction : natural := payload_remainder;
    begin
        for unit_index in 0 to 127 loop
            if remaining<=valid then return used+remaining; end if;
            remaining:=remaining-valid; used:=used+transfer_unit_size;
            fraction:=fraction+payload_remainder; valid:=payload_floor;
            if fraction>=40 then fraction:=fraction-40; valid:=valid+1; end if;
        end loop;
        assert false report "Unsupported active line allocation" severity failure;
        return 1;
    end function;
    constant active_symbols : positive := active_length;
    constant bs_position : positive := active_symbols+1;
    -- M/N=11/20, 2200x1125 total, 1920x1080 active, positive H/V sync.
    -- Hstart=44+148, Vstart=5+36; MISC0=RGB 8 bpc, synchronous clock.
    constant msa0 : symbol_array(0 to 20) := (
        SS,SS,timestamp_byte(mvid,2),timestamp_byte(mvid,1),timestamp_byte(mvid,0),'0'&x"08",'0'&x"98",
        '0'&x"04",'0'&x"65",'0'&x"00",'0'&x"2C",timestamp_byte(mvid,2),timestamp_byte(mvid,1),
        timestamp_byte(mvid,0),'0'&x"07",'0'&x"80",'0'&x"04",'0'&x"38",'0'&x"00",'0'&x"00",SE);
    constant msa1 : symbol_array(0 to 20) := (
        SS,SS,timestamp_byte(mvid,2),timestamp_byte(mvid,1),timestamp_byte(mvid,0),'0'&x"00",'0'&x"C0",
        '0'&x"00",'0'&x"29",'0'&x"00",'0'&x"05",timestamp_byte(mvid,2),timestamp_byte(mvid,1),
        timestamp_byte(mvid,0),timestamp_byte(nvid,2),timestamp_byte(nvid,1),timestamp_byte(nvid,0),'0'&x"21",'0'&x"00",'0'&x"00",SE);
    type font_type is array(0 to 15) of std_logic_vector(34 downto 0);
    constant font : font_type := (
        "01110100011000110001100011000101110", -- 0
        "00100011000010000100001000010001110", -- 1
        "01110100010000100010001000100011111", -- 2
        "11110000010000101110000010000111110", -- 3
        "00010001100101010010111110001000010", -- 4
        "11111100001000011110000010000111110", -- 5
        "01110100001000011110100011000101110", -- 6
        "11111000010001000100010000100001000", -- 7
        "01110100011000101110100011000101110", -- 8
        "01110100011000101111000010000101110", -- 9
        "01110100011000111111100011000110001", -- A
        "11110100011000111110100011000111110", -- B
        "01111100001000010000100001000001111", -- C
        "11110100011000110001100011000111110", -- D
        "11111100001000011110100001000011111", -- E
        "11111100001000011110100001000010000");-- F
    signal cycle_x : natural range 0 to 1999 := 0;
    signal line_y : natural range 0 to 1124 := 0;
    signal frame : unsigned(15 downto 0) := to_unsigned(initial_frame,16);
    signal tu_pos : natural range 0 to transfer_unit_size-1 := 0;
    signal tu_valid : natural range payload_floor to payload_floor+1 := payload_floor;
    signal tu_fraction : natural range 0 to 39 := payload_remainder;
    signal component_index : natural range 0 to 2 := 0;
    signal pixel_pair : natural range 0 to 960 := 0;
    type descriptor is record
        x : natural range 0 to 1918;
        y : natural range 0 to 1079;
        f : unsigned(15 downto 0);
        component_index : natural range 0 to 2;
        pixel : std_logic;
        s0,s1 : symbol;
    end record;
    constant empty_descriptor : descriptor := (0,0,(others=>'0'),0,'0',(others=>'0'),(others=>'0'));
    type descriptor_array is array(0 to 1) of descriptor;
    type colour_array is array(0 to 1) of rgb;
    signal scheduled, prepared, coloured : descriptor_array := (others=>empty_descriptor);
    signal colour0,colour1 : colour_array := (others=>(others=>'0'));
    type pixel_description is record
        colour : rgb;
        glyph : std_logic;
        glyph_bit : natural range 0 to 34;
        glyph_digit : natural range 0 to 15;
        border : std_logic;
    end record;
    type pixel_description_array is array(0 to 1) of pixel_description;
    constant empty_pixel : pixel_description := ((others=>'0'),'0',0,0,'0');
    signal pixel0,pixel1 : pixel_description_array := (others=>empty_pixel);
    signal valid_pipe, switch_pipe : std_logic_vector(2 downto 0) := "000";

    function prepare_pixel(x,y : natural; f : unsigned(15 downto 0)) return pixel_description is
        variable colour : rgb;
        variable info : pixel_description := empty_pixel;
        variable digit : natural range 0 to 3;
        variable nibble : natural range 0 to 15;
        variable gx : natural range 0 to 5;
        variable gy : natural range 0 to 6;
        variable glyph_left : natural range 32 to 104;
        variable box_x : natural range 32 to 1562;
    begin
        -- Comparators avoid inferred constant-division multipliers on pixel paths.
        if x<240 then colour:=x"FFFFFF";
        elsif x<480 then colour:=x"FFFF00";
        elsif x<720 then colour:=x"00FFFF";
        elsif x<960 then colour:=x"00FF00";
        elsif x<1200 then colour:=x"FF00FF";
        elsif x<1440 then colour:=x"FF0000";
        elsif x<1680 then colour:=x"0000FF";
        else colour:=x"000000"; end if;
        if y>=720 then
            if ((x/16)+(y/16)) mod 2=0 then colour:=x"202020";
            else colour:=x"E0E0E0"; end if;
        end if;
        -- Adjacent-pixel alternation makes swapped/duplicated lanes observable.
        if y>=960 then
            if x mod 2=0 then colour:=x"FF4000"; else colour:=x"0040FF"; end if;
        end if;
        box_x := to_integer(f(7 downto 0))*6+32;
        if x>=box_x and x<box_x+96 and y>=768 and y<832 then colour:=x"00FFFF"; end if;
        -- Four hexadecimal digits, frame number modulo 65536.
        if x>=32 and x<128 and y>=32 and y<60 then
            if x<56 then digit:=0; glyph_left:=32;
            elsif x<80 then digit:=1; glyph_left:=56;
            elsif x<104 then digit:=2; glyph_left:=80;
            else digit:=3; glyph_left:=104; end if;
            gx:=(x-glyph_left)/4; gy:=(y-32)/4;
            nibble:=to_integer(f(15-digit*4 downto 12-digit*4));
            colour:=x"000000";
            if gx<5 then
                info.glyph:='1'; info.glyph_digit:=nibble; info.glyph_bit:=34-(gy*5+gx);
            end if;
        end if;
        if x=0 or x=1919 or y=0 or y=1079 then info.border:='1'; end if;
        info.colour:=colour;
        return info;
    end function;
    function finish_pixel(info : pixel_description) return rgb is
        variable colour : rgb;
    begin
        colour:=info.colour;
        if info.glyph='1' and font(info.glyph_digit)(info.glyph_bit)='1' then colour:=x"00FF00"; end if;
        if info.border='1' then colour:=x"FFFFFF"; end if;
        return colour;
    end function;
begin
    assert timestamp_scale=1 or timestamp_scale=2048 report "Unreviewed timestamp representation" severity failure;
    assert transfer_unit_size=40 or transfer_unit_size=64 report "Unreviewed TU size" severity failure;
    assert bs_position mod 2=0 and bs_position+10<3520 report "Boundary/MSA collision" severity failure;
    stream_channel_count <= "010";
    process(clk)
        variable packet : std_logic_vector(72 downto 0);
        variable s0,s1 : symbol;
        variable phase,tpos,tvalid,fraction,comp,pair_index : natural;
        variable descriptors : descriptor_array;
    begin
        if rising_edge(clk) then
            if reset='1' then
                cycle_x<=0; line_y<=0; frame<=to_unsigned(initial_frame,16);
                tu_pos<=0; tu_valid<=payload_floor; tu_fraction<=payload_remainder;
                component_index<=0; pixel_pair<=0;
                ready<='0'; data<=(others=>'0');
                scheduled<=(others=>empty_descriptor); prepared<=(others=>empty_descriptor); coloured<=(others=>empty_descriptor);
                colour0<=(others=>(others=>'0')); colour1<=(others=>(others=>'0'));
                pixel0<=(others=>empty_pixel); pixel1<=(others=>empty_pixel);
                valid_pipe<="000"; switch_pipe<="000";
            else
                -- Four bounded stages: scheduling, geometry, glyph lookup, packing.
                -- Controls and the idle-switch marker travel with the same latency.
                ready<=valid_pipe(2); valid_pipe<=valid_pipe(1 downto 0)&'1';
                prepared<=scheduled; coloured<=prepared;
                packet:=(others=>'0'); packet(72):=switch_pipe(2);
                for slot in 0 to 1 loop
                    pixel0(slot)<=prepare_pixel(scheduled(slot).x,scheduled(slot).y,scheduled(slot).f);
                    pixel1(slot)<=prepare_pixel(scheduled(slot).x+1,scheduled(slot).y,scheduled(slot).f);
                    colour0(slot)<=finish_pixel(pixel0(slot));
                    colour1(slot)<=finish_pixel(pixel1(slot));
                    s0:=coloured(slot).s0; s1:=coloured(slot).s1;
                    if coloured(slot).pixel='1' then
                        case coloured(slot).component_index is
                            when 0 => s0:='0'&colour0(slot)(23 downto 16); s1:='0'&colour1(slot)(23 downto 16);
                            when 1 => s0:='0'&colour0(slot)(15 downto 8); s1:='0'&colour1(slot)(15 downto 8);
                            when others => s0:='0'&colour0(slot)(7 downto 0); s1:='0'&colour1(slot)(7 downto 0);
                        end case;
                    end if;
                    packet(slot*9+8 downto slot*9):=s0;
                    packet(18+slot*9+8 downto 18+slot*9):=s1;
                end loop;
                data<=packet;
                switch_pipe(2 downto 1)<=switch_pipe(1 downto 0); switch_pipe(0)<='0';
                if line_y=1080 and cycle_x<1700 then switch_pipe(0)<='1'; end if;
                descriptors:=(others=>empty_descriptor);
                tpos:=tu_pos; tvalid:=tu_valid; fraction:=tu_fraction;
                comp:=component_index; pair_index:=pixel_pair;
                for slot in 0 to 1 loop
                    phase:=cycle_x*2+slot;
                    s0:='0'&x"00"; s1:=s0;
                    if phase=0 then
                        tpos:=0; tvalid:=payload_floor; fraction:=payload_remainder;
                        comp:=0; pair_index:=0;
                        if line_y<1080 then s0:=BE; s1:=BE; end if;
                    elsif line_y<1080 and phase<=active_symbols then
                        if tpos<tvalid then
                            descriptors(slot).x:=pair_index*2;
                            descriptors(slot).y:=line_y;
                            descriptors(slot).f:=frame;
                            descriptors(slot).component_index:=comp;
                            descriptors(slot).pixel:='1';
                            if comp=2 then comp:=0; pair_index:=pair_index+1;
                            else comp:=comp+1; end if;
                        elsif tpos=tvalid then s0:=FS; s1:=FS;
                        elsif tpos=transfer_unit_size-1 then s0:=FE; s1:=FE;
                        end if;
                        if tpos=transfer_unit_size-1 then
                            tpos:=0; fraction:=fraction+payload_remainder; tvalid:=payload_floor;
                            if fraction>=40 then fraction:=fraction-40; tvalid:=tvalid+1; end if;
                        else tpos:=tpos+1; end if;
                    elsif phase=bs_position then
                        -- BS in slot 0 also matches the existing SR inserter.
                        s0:=BS; s1:=BS;
                    elsif phase>=bs_position+1 and phase<=bs_position+6 then
                        -- Four copies in TOTAL: two VB-ID/Mvid/Maud triples/lane.
                        case phase-bs_position is
                            when 1|4 =>
                                if line_y>=1079 and line_y<1124 then s0:='0'&x"11";
                                else s0:='0'&x"10"; end if; -- mute audio
                            when 2|5 => s0:=timestamp_byte(mvid,0);
                            when others => s0:='0'&x"00";
                        end case;
                        s1:=s0;
                    elsif line_y=1080 and phase>=3520 and phase<=3540 then
                        s0:=msa0((cycle_x mod 32)*2+slot); s1:=msa1((cycle_x mod 32)*2+slot);
                    end if;
                    descriptors(slot).s0:=s0; descriptors(slot).s1:=s1;
                end loop;
                scheduled<=descriptors;
                tu_pos<=tpos; tu_valid<=tvalid; tu_fraction<=fraction;
                component_index<=comp; pixel_pair<=pair_index;
                if cycle_x=1999 then
                    cycle_x<=0;
                    if line_y=1124 then line_y<=0; frame<=frame+1;
                    else line_y<=line_y+1; end if;
                else cycle_x<=cycle_x+1; end if;
            end if;
        end if;
    end process;
end architecture;
