-- ========================================================
-- VIBE-FASHION 초기 데이터 (Seed Data)
-- ========================================================

-- --------------------------------------------------------
-- 1. 카테고리 7개 등록
-- 상의(top), 하의(bottom), 아우터(outer), 원피스/세트(dress), 액세서리(acc), 가방(bag), 신발(shoes)
-- --------------------------------------------------------
INSERT INTO public.categories (name, slug, sort_order)
VALUES 
    ('상의', 'top', 1),
    ('하의', 'bottom', 2),
    ('아우터', 'outer', 3),
    ('원피스/세트', 'dress', 4),
    ('액세서리', 'acc', 5),
    ('가방', 'bag', 6),
    ('신발', 'shoes', 7)
ON CONFLICT (slug) DO UPDATE 
SET name = EXCLUDED.name, sort_order = EXCLUDED.sort_order;


-- --------------------------------------------------------
-- 2. 샘플 상품 4개 등록
-- --------------------------------------------------------
DO $$
DECLARE
    v_top_id BIGINT;
    v_bottom_id BIGINT;
    v_outer_id BIGINT;
    v_dress_id BIGINT;
    v_shoes_id BIGINT;
    v_acc_id BIGINT;
    
    v_prod_crop_id UUID;
    v_prod_pants_id UUID;
    v_prod_jacket_id UUID;
    v_prod_dress_id UUID;
    v_prod_shoes_id UUID;
    v_prod_coat_id UUID;
    v_prod_hat_id UUID;

    colors TEXT[] := ARRAY['블랙', '화이트', '베이지'];
    sizes TEXT[] := ARRAY['S', 'M', 'L'];
    c TEXT;
    s TEXT;
BEGIN
    -- 카테고리 ID 조회
    SELECT id INTO v_top_id FROM public.categories WHERE slug = 'top';
    SELECT id INTO v_bottom_id FROM public.categories WHERE slug = 'bottom';
    SELECT id INTO v_outer_id FROM public.categories WHERE slug = 'outer';
    SELECT id INTO v_dress_id FROM public.categories WHERE slug = 'dress';
    SELECT id INTO v_shoes_id FROM public.categories WHERE slug = 'shoes';
    SELECT id INTO v_acc_id FROM public.categories WHERE slug = 'acc';

    -- 상품 1: 베이직 크롭 티셔츠 (정가 29,900원, 판매/할인가 19,900원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_top_id, 
        '베이직 크롭 티셔츠', 
        '트렌디하고 슬림한 실루엣의 베이직 크롭 티셔츠입니다. 부드러운 코튼 소재로 제작되었습니다.', 
        29900, 
        19900, 
        true,
        true
    )
    RETURNING id INTO v_prod_crop_id;

    -- 상품 2: 와이드 데님 팬츠 (39,900원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_bottom_id, 
        '와이드 데님 팬츠', 
        '편안하면서도 멋스러운 와이드 핏 데님 팬츠입니다. 다양한 상의와 매치하기 좋습니다.', 
        39900, 
        NULL, 
        true,
        true
    )
    RETURNING id INTO v_prod_pants_id;

    -- 상품 3: 오버핏 코튼 자켓 (59,900원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_outer_id, 
        '오버핏 코튼 자켓', 
        '자연스러운 핏감이 매력적인 오버사이즈 코튼 자켓입니다. 간절기 아우터로 추천합니다.', 
        59900, 
        NULL, 
        true,
        true
    )
    RETURNING id INTO v_prod_jacket_id;

    -- 상품 4: 플로럴 미디 원피스 (45,900원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_dress_id, 
        '플로럴 미디 원피스', 
        '화사한 플라워 패턴이 돋보이는 페미닌한 무드의 미디 기장 원피스입니다.', 
        45900, 
        NULL, 
        true,
        true
    )
    RETURNING id INTO v_prod_dress_id;

    -- 상품 5: 클래식 레더 스니커즈 (신발 - 69,000원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_shoes_id, 
        '클래식 레더 스니커즈', 
        '깔끔하고 편안한 착화감의 데일리 레더 스니커즈입니다.', 
        79000, 
        69000, 
        true,
        true
    )
    RETURNING id INTO v_prod_shoes_id;

    -- 상품 6: 울 블렌드 싱글 코트 (코트 - 129,000원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_outer_id, 
        '울 블렌드 싱글 코트', 
        '보온성과 스타일을 겸비한 모던한 핏의 프리미엄 싱글 코트입니다.', 
        149000, 
        129000, 
        true,
        true
    )
    RETURNING id INTO v_prod_coat_id;

    -- 상품 7: 코튼 베이직 볼캡 모자 (모자 - 19,000원)
    INSERT INTO public.products (category_id, name, description, price, sale_price, is_active, is_featured)
    VALUES (
        v_acc_id, 
        '코튼 베이직 볼캡 모자', 
        '어디에나 자연스럽게 매치하기 좋은 사계절 데일리 코튼 볼캡입니다.', 
        25000, 
        19000, 
        true,
        true
    )
    RETURNING id INTO v_prod_hat_id;

    -- ----------------------------------------------------
    -- 3. 첫 번째 상품(베이직 크롭 티셔츠) 옵션 9개 등록
    -- (블랙/화이트/베이지 × S/M/L)
    -- ----------------------------------------------------
    FOREACH c IN ARRAY colors
    LOOP
        FOREACH s IN ARRAY sizes
        LOOP
            INSERT INTO public.product_options (product_id, option_name, option_value, additional_price, stock_quantity)
            VALUES (
                v_prod_crop_id, 
                '색상/사이즈', 
                c || ' / ' || s, 
                0, 
                50 -- 옵션별 기본 재고 50개
            );
        END LOOP;
    END LOOP;

    -- ----------------------------------------------------
    -- 4. 상품 썸네일 이미지 등록 (picsum.photos 무료 이미지)
    -- ----------------------------------------------------
    -- 상품 1: 베이직 크롭 티셔츠
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_crop_id, 'https://picsum.photos/id/1062/600/800', true, 1),
        (v_prod_crop_id, 'https://picsum.photos/id/1059/600/800', false, 2);

    -- 상품 2: 와이드 데님 팬츠
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_pants_id, 'https://picsum.photos/id/1070/600/800', true, 1);

    -- 상품 3: 오버핏 코튼 자켓
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_jacket_id, 'https://picsum.photos/id/1058/600/800', true, 1);

    -- 상품 4: 플로럴 미디 원피스
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_dress_id, 'https://picsum.photos/id/1069/600/800', true, 1);

    -- 상품 5: 클래식 레더 스니커즈 (신발)
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_shoes_id, 'https://images.unsplash.com/photo-1549298916-b41d501d3772?w=600&auto=format&fit=crop&q=80', true, 1);

    -- 상품 6: 울 블렌드 싱글 코트 (코트)
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_coat_id, 'https://images.unsplash.com/photo-1544923246-77307dd654cb?w=600&auto=format&fit=crop&q=80', true, 1);

    -- 상품 7: 코튼 베이직 볼캡 모자 (모자)
    INSERT INTO public.product_images (product_id, image_url, is_primary, sort_order)
    VALUES 
        (v_prod_hat_id, 'https://images.unsplash.com/photo-1588850561407-ed78c282e89b?w=600&auto=format&fit=crop&q=80', true, 1);

END $$;
